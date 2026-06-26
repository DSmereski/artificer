/**
 * plugins/terminal.ts — multi-session PowerShell terminal panel (P3).
 *
 * Was a single shell; now a tabbed manager over N independent {@link TermSession}
 * instances. A tab strip lets the operator switch between live shells and spawn
 * (＋) or close (×) them. The gateway spawns one PowerShell per WS connection,
 * so each tab is its own connection — capped server-side by
 * `terminal_max_sessions` (8). Background tabs stay connected so their state is
 * preserved; suspending the panel (hidden / gaming) disconnects every session.
 *
 * Security: every session connects to the same loopback-only, Bearer-authed
 * ws://127.0.0.1:8766/v1/term. The gateway enforces loopback + token + the
 * session cap server-side regardless of what this client does.
 *
 * The three pure protocol helpers live in term-protocol.ts; they are re-exported
 * here so existing unit tests keep importing them from this module.
 */

import '@xterm/xterm/css/xterm.css';
import './terminal.css';  /* terminal panel styles — kept in sync with index.html <style> */
import { register } from './registry.js';
import { getBearerToken, getBoardSessionToken } from '../gateway.js';

// The wallpaper runs on loopback with no device Bearer, so fall back to the
// board session-token (the gateway's /v1/term accepts it for loopback). Primed
// once on mount; the device token wins if one is set.
let _termToken: string | null = null;
function _token(): string | null { return getBearerToken() || _termToken; }
import { TermSession, DEFAULT_TERM_FONT_SIZE } from './term-session.js';
import { sessionLabel } from './term-protocol.js';
import { resolveSettings } from './instances.js';
import type { PanelPlugin, RelevanceResult, Rect } from './contract.js';
import type { SystemState, RenderBudget } from '../state/types.js';

// ─── Settings ─────────────────────────────────────────────────────────────────

interface TerminalSettings {
  /** xterm font size in px. Default 13 == today's hardcoded value. */
  fontSize: number;
}

const DEFAULT_SETTINGS: TerminalSettings = { fontSize: DEFAULT_TERM_FONT_SIZE };

/** Effective font size for the terminal cell (single-instance friendly). */
function _fontSize(): number {
  const { fontSize } = resolveSettings(_rootEl, 'terminal', DEFAULT_SETTINGS);
  return fontSize > 0 ? fontSize : DEFAULT_TERM_FONT_SIZE;
}

export {
  encodeInputFrame,
  buildResizeFrame,
  calcFitDimensions,
} from './term-protocol.js';

// Client-side ceiling — matches the gateway's terminal_max_sessions default.
const MAX_SESSIONS = 8;

// ─── Manager state ────────────────────────────────────────────────────────────

let _rootEl: HTMLElement | null = null;
let _tabStrip: HTMLElement | null = null;
let _addBtn: HTMLButtonElement | null = null;
let _sessionHost: HTMLElement | null = null;
const _sessions = new Map<string, TermSession>();
let _order: string[] = []; // tab order (session ids)
let _activeId: string | null = null;
let _seq = 0; // monotonic counter for unique ids + PS-N labels
let _suspended = false;

// ─── Session CRUD ─────────────────────────────────────────────────────────────

function _spawnSession(): TermSession | null {
  if (_sessions.size >= MAX_SESSIONS || !_sessionHost) return null;
  _seq += 1;
  const id = `ps-${_seq}`;
  const session = new TermSession(id, sessionLabel(_seq), _renderTabs, _fontSize());
  _sessions.set(id, session);
  _order.push(id);
  _sessionHost.appendChild(session.el);
  session.init().catch((err) => {
    console.error(`[terminal] session ${id} xterm init failed:`, err);
    session.setOffline();
  });
  _driveSession(session);
  _setActive(id);
  return session;
}

function _closeSession(id: string): void {
  const session = _sessions.get(id);
  if (!session) return;
  session.dispose();
  _sessions.delete(id);
  _order = _order.filter((x) => x !== id);

  if (_activeId === id) {
    _activeId = null;
    const next = _order[_order.length - 1] ?? null;
    if (next) _setActive(next);
  }
  // Never leave the panel with zero shells.
  if (_sessions.size === 0) _spawnSession();
  else _renderTabs();
}

function _setActive(id: string): void {
  if (!_sessions.has(id)) return;
  _activeId = id;
  for (const [sid, s] of _sessions) s.setVisible(sid === id);
  _renderTabs();
}

// ─── Token / gateway gating for one session ───────────────────────────────────

function _driveSession(session: TermSession): void {
  if (_suspended) return;
  const token = _token();
  if (!token) {
    session.setNoToken();
    return;
  }
  if (!session.connected) session.connect(token);
}

// ─── Tab strip render ─────────────────────────────────────────────────────────

function _renderTabs(): void {
  if (!_tabStrip) return;
  // F3: tab strip gets inset-sheen recessed-track treatment
  _tabStrip.classList.add('fx3-tab-strip');
  _tabStrip.innerHTML = '';
  for (const id of _order) {
    const s = _sessions.get(id);
    if (!s) continue;
    const isActive = id === _activeId;
    const tab = document.createElement('button');
    // F3: active tab gets chrome-bevel lift class
    tab.className = 'term-tab' + (isActive ? ' is-active fx3-tab-active' : '');
    tab.type = 'button';
    tab.title = `${s.label} — ${s.status}`;

    const dot = document.createElement('span');
    // F3: connected dot gets heartbeat pulse
    dot.className = 'term-tab-dot' + (s.connected ? ' fx3-heartbeat' : '');
    dot.style.color = s.connected ? 'var(--green)' : 'var(--faint)';
    dot.textContent = s.connected ? '●' : '○';

    const name = document.createElement('span');
    name.className = 'term-tab-label';
    name.textContent = s.label;

    tab.append(dot, name);

    if (_sessions.size > 1) {
      const close = document.createElement('span');
      close.className = 'term-tab-close';
      close.textContent = '×';
      close.title = `Close ${s.label}`;
      close.addEventListener('click', (e) => {
        e.stopPropagation();
        _closeSession(id);
      });
      tab.appendChild(close);
    }

    tab.addEventListener('click', () => _setActive(id));
    _tabStrip.appendChild(tab);
  }
  if (_addBtn) _addBtn.disabled = _sessions.size >= MAX_SESSIONS;
}

// ─── Relevance ────────────────────────────────────────────────────────────────

function relevance(state: SystemState): RelevanceResult {
  // Hide when gaming (don't steal focus or hold shells).
  if (state.tier === 'gaming') return { priority: 0, size: 'hidden' };
  // Unconfigured (no device token) → small connect chip, not a full cell.
  if (!_token()) return { priority: 16, size: 'sm' };
  return { priority: 35, size: 'md' };
}

// ─── Mount ────────────────────────────────────────────────────────────────────

function mount(el: HTMLElement): void {
  _rootEl = el;
  el.innerHTML = `
    <div class="panel-header term-header">
      <span class="panel-label">TERMINAL</span>
      <div class="term-tabs" data-role="tabs"></div>
      <button type="button" class="term-add" data-role="add" title="New PowerShell session">＋</button>
    </div>
    <div class="term-sessions" data-role="sessions"></div>
  `;
  _tabStrip = el.querySelector('[data-role="tabs"]');
  _addBtn = el.querySelector('[data-role="add"]');
  _sessionHost = el.querySelector('[data-role="sessions"]');

  _addBtn?.addEventListener('click', () => _spawnSession());

  // Seed the first session if none survive a previous mount.
  if (_sessions.size === 0) _spawnSession();
  else {
    for (const s of _sessions.values()) _sessionHost?.appendChild(s.el);
    if (_activeId) _setActive(_activeId);
    _renderTabs();
  }

  // No device Bearer on the wallpaper → prime the loopback board session-token,
  // then connect any sessions that were waiting on a token.
  if (!getBearerToken() && !_termToken) {
    getBoardSessionToken().then((t) => {
      if (t) {
        _termToken = t;
        for (const s of _sessions.values()) _driveSession(s);
        _renderTabs();
      }
    });
  }
}

// ─── Update ───────────────────────────────────────────────────────────────────

function update(state: SystemState, _budget: RenderBudget): void {
  if (!_rootEl || _suspended) return;

  // Apply the (possibly gear-changed) font size to every live session.
  const fs = _fontSize();
  for (const s of _sessions.values()) s.setFontSize(fs);

  const token = _token();
  for (const s of _sessions.values()) {
    if (!token) {
      s.setNoToken();
    } else if (!s.connected && state.gatewayUp) {
      s.connect(token);
    } else if (!s.connected && !state.gatewayUp) {
      s.setOffline();
    }
  }
  _renderTabs(); // repaint connection dots
}

// ─── onResize ─────────────────────────────────────────────────────────────────

function onResize(_rect: Rect): void {
  // Only the visible (active) session needs refitting.
  if (_activeId) _sessions.get(_activeId)?.fit();
}

// ─── suspend / resume ─────────────────────────────────────────────────────────

function suspend(): void {
  _suspended = true;
  for (const s of _sessions.values()) s.suspend();
}

function resume(): void {
  _suspended = false;
  for (const s of _sessions.values()) {
    s.resume();
    _driveSession(s);
  }
}

// ─── Plugin definition ────────────────────────────────────────────────────────

const terminalPlugin: PanelPlugin = {
  id:          'terminal',
  title:       'TERMINAL',
  dataSources: [{ kind: 'state' }],
  relevance,
  mount,
  update,
  onResize,
  suspend,
  resume,
  defaultSettings: { ...DEFAULT_SETTINGS },
  settingsSchema: {
    fields: [
      {
        key: 'fontSize',
        label: 'Font size (px)',
        type: 'number',
        default: DEFAULT_TERM_FONT_SIZE,
        hint: 'xterm font size for all sessions',
      },
    ],
  },
};

register(terminalPlugin);
export { terminalPlugin };
