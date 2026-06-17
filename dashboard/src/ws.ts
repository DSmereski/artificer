/**
 * ws.ts — WebSocket manager for /v1/events (Bearer-authed live event feed).
 *
 * Connects to WS /v1/events?token=<bearer> with exponential-backoff reconnect.
 * Suspends when the governor says ws:'board' or ws:'none', or when paused/hidden.
 *
 * On each valid frame, calls the registered frame handler.
 * Silently drops malformed JSON.
 *
 * Usage:
 *   createEventWS({ onFrame, onBoard, getToken, getWsMode })
 *   ws.suspend() / ws.resume()
 *
 * Board WS (/board/events) is handled by a separate light connection also
 * managed here (open endpoint — no token required).
 */

import { isPaused } from './pause.js';

// ─── Re-export pure frame-shaping helpers (from ws_frames.ts) ────────────────
// ws_frames.ts has no DOM/window side effects → safe to unit-test in Node.

export {
  shapeFrameToTicker,
  shapeBoardFrameToTicker,
  type V1EventFrame,
  type BoardEventFrame,
  type TickerEvent,
} from './ws_frames.js';

import type {
  V1EventFrame,
  BoardEventFrame,
} from './ws_frames.js';

// ─── WS URLs ──────────────────────────────────────────────────────────────────

const BASE_URL: string = import.meta.env.DEV
  ? 'ws://127.0.0.1:8766'
  : 'ws://127.0.0.1:8766';

// ─── Backoff ──────────────────────────────────────────────────────────────────

const BACKOFF_INIT_MS = 1_000;
const BACKOFF_MAX_MS  = 60_000;
const BACKOFF_MULT    = 2;

function nextBackoff(prev: number): number {
  return Math.min(prev * BACKOFF_MULT, BACKOFF_MAX_MS);
}

// ─── WS mode ─────────────────────────────────────────────────────────────────

type WsMode = 'all' | 'board' | 'none';

export interface EventWSOptions {
  /** Called for each valid /v1/events frame. */
  onFrame: (frame: V1EventFrame) => void;
  /** Called for each valid /board/events frame. */
  onBoard: (frame: BoardEventFrame) => void;
  /** Return the current bearer token (or null if unset). */
  getToken: () => string | null;
  /** Return the current WS mode from the governor budget. */
  getWsMode: () => WsMode;
}

export interface EventWSHandle {
  suspend(): void;
  resume(): void;
}

/**
 * Create and manage both WS connections.
 * Returns a handle for suspend/resume (governor pause integration).
 */
export function createEventWS(opts: EventWSOptions): EventWSHandle {
  let _suspended = false;

  // ── /v1/events WS ────────────────────────────────────────────────────────────

  let _v1ws: WebSocket | null = null;
  let _v1Backoff = BACKOFF_INIT_MS;
  let _v1ReconnectTimer: ReturnType<typeof setTimeout> | null = null;

  function _connectV1(): void {
    if (_suspended || isPaused() || document.hidden) return;
    if (opts.getWsMode() === 'none') return;

    const token = opts.getToken();
    if (!token) {
      // No token — retry after a longer interval; the board WS still works
      _v1ReconnectTimer = setTimeout(_connectV1, 10_000);
      return;
    }

    const url = `${BASE_URL}/v1/events?token=${encodeURIComponent(token)}`;
    try {
      _v1ws = new WebSocket(url);
    } catch {
      _scheduleV1Reconnect();
      return;
    }

    _v1ws.onopen = () => {
      _v1Backoff = BACKOFF_INIT_MS; // reset backoff on success
    };

    _v1ws.onmessage = (ev) => {
      let frame: V1EventFrame;
      try {
        frame = JSON.parse(ev.data as string) as V1EventFrame;
      } catch {
        return; // drop malformed JSON silently
      }
      if (!frame || typeof frame.type !== 'string') return;
      opts.onFrame(frame);
    };

    _v1ws.onerror = () => {
      // Handled via onclose
    };

    _v1ws.onclose = () => {
      _v1ws = null;
      if (!_suspended) _scheduleV1Reconnect();
    };
  }

  function _scheduleV1Reconnect(): void {
    if (_suspended) return;
    _v1ReconnectTimer = setTimeout(() => {
      _v1ReconnectTimer = null;
      _v1Backoff = nextBackoff(_v1Backoff);
      _connectV1();
    }, _v1Backoff);
  }

  function _disconnectV1(): void {
    if (_v1ReconnectTimer !== null) {
      clearTimeout(_v1ReconnectTimer);
      _v1ReconnectTimer = null;
    }
    if (_v1ws) {
      _v1ws.onclose = null;
      _v1ws.close();
      _v1ws = null;
    }
  }

  // ── /board/events WS ─────────────────────────────────────────────────────────

  let _boardWs: WebSocket | null = null;
  let _boardBackoff = BACKOFF_INIT_MS;
  let _boardReconnectTimer: ReturnType<typeof setTimeout> | null = null;

  function _connectBoard(): void {
    if (_suspended || isPaused() || document.hidden) return;
    if (opts.getWsMode() === 'none') return;

    const url = `${BASE_URL}/board/events`;
    try {
      _boardWs = new WebSocket(url);
    } catch {
      _scheduleBoardReconnect();
      return;
    }

    _boardWs.onopen = () => {
      _boardBackoff = BACKOFF_INIT_MS;
    };

    _boardWs.onmessage = (ev) => {
      let frame: BoardEventFrame;
      try {
        frame = JSON.parse(ev.data as string) as BoardEventFrame;
      } catch {
        return;
      }
      if (!frame || typeof frame.type !== 'string') return;
      opts.onBoard(frame);
    };

    _boardWs.onerror = () => {
      // Handled via onclose
    };

    _boardWs.onclose = () => {
      _boardWs = null;
      if (!_suspended) _scheduleBoardReconnect();
    };
  }

  function _scheduleBoardReconnect(): void {
    if (_suspended) return;
    _boardReconnectTimer = setTimeout(() => {
      _boardReconnectTimer = null;
      _boardBackoff = nextBackoff(_boardBackoff);
      _connectBoard();
    }, _boardBackoff);
  }

  function _disconnectBoard(): void {
    if (_boardReconnectTimer !== null) {
      clearTimeout(_boardReconnectTimer);
      _boardReconnectTimer = null;
    }
    if (_boardWs) {
      _boardWs.onclose = null;
      _boardWs.close();
      _boardWs = null;
    }
  }

  // ── Visibility / pause integration ────────────────────────────────────────────

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      _disconnectV1();
      _disconnectBoard();
    } else if (!_suspended) {
      _connectV1();
      _connectBoard();
    }
  });

  // ── Public interface ──────────────────────────────────────────────────────────

  function suspend(): void {
    _suspended = true;
    _disconnectV1();
    _disconnectBoard();
  }

  function resume(): void {
    _suspended = false;
    _connectV1();
    _connectBoard();
  }

  // ── Initial connect ───────────────────────────────────────────────────────────

  _connectV1();
  _connectBoard();

  return { suspend, resume };
}
