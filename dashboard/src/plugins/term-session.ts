/**
 * plugins/term-session.ts — a single PowerShell PTY-over-WS session.
 *
 * One TermSession owns exactly one xterm.js instance and one WebSocket to
 * ws://127.0.0.1:8766/v1/term (loopback-only, Bearer-authed; the gateway
 * spawns one shell per WS connection). The multi-session manager (terminal.ts)
 * holds a Map of these and shows/hides them as the operator switches tabs.
 *
 * Each session is independent: its own scrollback, its own reconnect backoff,
 * its own status. Background tabs stay CONNECTED (so their shell state is
 * preserved) until the whole panel is suspended (hidden / gaming), which
 * disconnects every session to release shells on the gateway.
 */

import {
  GW_WS_BASE,
  getTerminalTheme,
  BACKOFF_INIT_MS,
  nextBackoff,
  encodeInputFrame,
  buildResizeFrame,
} from './term-protocol.js';

type XTerminal = import('@xterm/xterm').Terminal;
type XFitAddon = import('@xterm/addon-fit').FitAddon;

export type TermStatus =
  | 'connecting'
  | 'connected'
  | 'offline'
  | 'no-token'
  | 'disabled';

const STATUS_MSG: Record<TermStatus, string> = {
  connecting: 'connecting…',
  connected:  '',
  offline:    'shell offline — retrying',
  'no-token': 'set deviceToken in Lively props to connect',
  disabled:   'suspended',
};

// Cache the dynamically-imported xterm classes so N sessions share one import.
let _xtermCtor: typeof import('@xterm/xterm').Terminal | null = null;
let _fitCtor: typeof import('@xterm/addon-fit').FitAddon | null = null;

async function _loadXterm(): Promise<void> {
  if (_xtermCtor && _fitCtor) return;
  const [{ Terminal }, { FitAddon }] = await Promise.all([
    import('@xterm/xterm'),
    import('@xterm/addon-fit'),
  ]);
  _xtermCtor = Terminal;
  _fitCtor = FitAddon;
}

/** Default xterm font size — matches the original hardcoded value. */
export const DEFAULT_TERM_FONT_SIZE = 13;

export class TermSession {
  readonly id: string;
  label: string;
  /** Root element — the manager appends this and toggles its `display`. */
  readonly el: HTMLDivElement;

  /** Desired xterm font size (px); applied at init + via setFontSize(). */
  private _fontSize = DEFAULT_TERM_FONT_SIZE;

  private _term: XTerminal | null = null;
  private _fit: XFitAddon | null = null;
  private _ws: WebSocket | null = null;
  private _suspended = false;
  private _backoff = BACKOFF_INIT_MS;
  private _reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private _token: string | null = null;
  private _status: TermStatus = 'no-token';
  private _statusEl: HTMLElement;
  private _xtermHost: HTMLElement;
  private readonly _onStatus: () => void;

  private readonly _themeHandler: () => void;

  constructor(id: string, label: string, onStatus: () => void, fontSize?: number) {
    this.id = id;
    this.label = label;
    this._onStatus = onStatus;
    if (typeof fontSize === 'number' && fontSize > 0) this._fontSize = fontSize;

    const root = document.createElement('div');
    root.className = 'term-session';
    root.dataset.sessionId = id;
    root.innerHTML = `
      <div class="term-status" data-role="status"></div>
      <div class="term-container" data-role="xterm"></div>
    `;
    this.el = root;
    this._statusEl = root.querySelector('[data-role="status"]') as HTMLElement;
    this._xtermHost = root.querySelector('[data-role="xterm"]') as HTMLElement;
    this._renderStatus();

    // Re-apply terminal colors when the dashboard theme changes.
    this._themeHandler = () => {
      if (this._term) this._term.options.theme = getTerminalTheme();
    };
    window.addEventListener('hive-theme-change', this._themeHandler);
  }

  get status(): TermStatus {
    return this._status;
  }

  get connected(): boolean {
    return this._ws !== null && this._ws.readyState === WebSocket.OPEN;
  }

  /** Lazy-init xterm into this session's host element. Idempotent. */
  async init(): Promise<void> {
    if (this._term) return;
    await _loadXterm();
    const Terminal = _xtermCtor!;
    const FitAddon = _fitCtor!;

    const term = new Terminal({
      fontFamily: '"JetBrains Mono", "Cascadia Code", "Fira Code", monospace',
      fontSize: this._fontSize,
      lineHeight: 1.2,
      theme: getTerminalTheme(),
      cursorBlink: true,
      cursorStyle: 'block',
      scrollback: 2000,
      convertEol: true,
      allowProposedApi: true,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(this._xtermHost);
    try { fit.fit(); } catch { /* host may be display:none */ }

    // Lively forwards keyboard into the wallpaper WebView, which double-delivers
    // each keystroke (a single `ls` arrived as `llss`). Drop a keydown that
    // exactly repeats the previous one within 25ms and is NOT an OS key-repeat
    // (e.repeat) — far faster than any human double-letter (~100ms+), so real
    // "ll"/"ss" survive while the phantom duplicate is swallowed.
    let _lastKey = '';
    let _lastTs = -1;
    term.attachCustomKeyEventHandler((e) => {
      if (e.type !== 'keydown') return true;
      if (!e.repeat && e.key === _lastKey && e.timeStamp - _lastTs < 25) {
        return false; // phantom duplicate dispatch
      }
      _lastKey = e.key;
      _lastTs = e.timeStamp;
      return true;
    });

    // Under Lively's synthetic keyboard, xterm reads the same character from
    // BOTH the keydown path and the textarea `input` event (its normal
    // suppression doesn't fire), so onData emits each key twice in the same
    // synchronous burst (`ls` → `llss`). Drop a payload identical to the
    // immediately-preceding one within 15ms — the phantom arrives <1ms later,
    // while a real double-letter is ≥80ms apart even at fast typing.
    let _lastData = '';
    let _lastDataT = -1;
    term.onData((data) => {
      const now = performance.now();
      // 50ms window: Lively's double-dispatch lands within ~1 frame (≤16ms),
      // while a real same-character double-press is ≥80ms apart even at fast
      // typing — so this drops the phantom without eating genuine "ll"/"ss".
      if (data === _lastData && now - _lastDataT < 50) {
        _lastDataT = now;
        return; // phantom duplicate dispatch
      }
      _lastData = data;
      _lastDataT = now;
      if (this._ws && this._ws.readyState === WebSocket.OPEN) {
        try { this._ws.send(encodeInputFrame(data)); } catch { /* closed */ }
      }
    });

    this._term = term;
    this._fit = fit;
  }

  /** Show or hide the session; on show, refit to the now-visible host. */
  setVisible(visible: boolean): void {
    this.el.style.display = visible ? '' : 'none';
    if (visible) this.fit();
  }

  /** Apply a new xterm font size live (and refit so dimensions stay correct). */
  setFontSize(px: number): void {
    if (!(px > 0) || px === this._fontSize) return;
    this._fontSize = px;
    if (this._term) {
      this._term.options.fontSize = px;
      this.fit();
    }
  }

  private _setStatus(s: TermStatus): void {
    if (s === this._status) return;
    this._status = s;
    this._renderStatus();
    this._onStatus();
  }

  private _renderStatus(): void {
    this._statusEl.textContent = STATUS_MSG[this._status];
    this._statusEl.style.display = this._status === 'connected' ? 'none' : 'flex';
    this._statusEl.className = `term-status term-status-${this._status}`;
  }

  // ─── WS lifecycle ─────────────────────────────────────────────────────────

  connect(token: string): void {
    if (this._suspended || this._ws) return;
    this._token = token;
    this._setStatus('connecting');

    let ws: WebSocket;
    try {
      ws = new WebSocket(`${GW_WS_BASE}/v1/term?token=${encodeURIComponent(token)}`);
      ws.binaryType = 'arraybuffer';
    } catch {
      this._scheduleReconnect(token);
      return;
    }
    this._ws = ws;

    ws.onopen = () => {
      this._backoff = BACKOFF_INIT_MS;
      this._setStatus('connected');
      this._sendResize();
    };
    ws.onmessage = (ev) => {
      if (!this._term) return;
      if (ev.data instanceof ArrayBuffer) this._term.write(new Uint8Array(ev.data));
      else if (typeof ev.data === 'string') this._term.write(ev.data);
    };
    ws.onclose = () => {
      this._ws = null;
      if (!this._suspended && this._token) {
        this._setStatus('offline');
        this._scheduleReconnect(this._token);
      }
    };
  }

  disconnect(): void {
    if (this._reconnectTimer !== null) {
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = null;
    }
    if (this._ws) {
      this._ws.onclose = null;
      this._ws.close();
      this._ws = null;
    }
  }

  private _scheduleReconnect(token: string): void {
    if (this._suspended) return;
    this._reconnectTimer = setTimeout(() => {
      this._reconnectTimer = null;
      this._backoff = nextBackoff(this._backoff);
      this.connect(token);
    }, this._backoff);
  }

  private _sendResize(): void {
    if (!this._ws || this._ws.readyState !== WebSocket.OPEN || !this._term) return;
    try {
      this._ws.send(buildResizeFrame(this._term.cols, this._term.rows));
    } catch { /* best effort */ }
  }

  fit(): void {
    if (!this._fit) return;
    try {
      this._fit.fit();
      this._sendResize();
    } catch { /* host hidden */ }
  }

  suspend(): void {
    this._suspended = true;
    this.disconnect();
    this._setStatus('disabled');
  }

  resume(): void {
    this._suspended = false;
    // The manager re-drives connect() on the next update tick.
  }

  setNoToken(): void {
    this.disconnect();
    this._setStatus('no-token');
  }

  setOffline(): void {
    this._setStatus('offline');
  }

  /** Tear down completely (tab closed). */
  dispose(): void {
    this.disconnect();
    window.removeEventListener('hive-theme-change', this._themeHandler);
    if (this._term) {
      try { this._term.dispose(); } catch { /* ignore */ }
      this._term = null;
    }
    this.el.remove();
  }
}
