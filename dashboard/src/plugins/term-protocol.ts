/**
 * plugins/term-protocol.ts — pure protocol + theme for the PowerShell terminal.
 *
 * No DOM, no WebSocket — just the frame codec, fit math, palette, and backoff
 * constants shared by every terminal session. Unit-testable in Node. Extracted
 * from terminal.ts when the panel went multi-session (P3); terminal.ts re-exports
 * the three pure helpers so existing tests keep importing them from there.
 */

// ─── WS URL base ──────────────────────────────────────────────────────────────

export const GW_WS_BASE = 'ws://127.0.0.1:8766';

// ─── Canon palette ────────────────────────────────────────────────────────────

// Terminal palette derived from CSS theme vars where possible.
// background/foreground/cursor track the dashboard theme;
// the 16 ANSI color slots keep semantically-stable hues (red=error, green=ok)
// while key slots (bg/fg/cursor/yellow) follow the active theme.
function _cv(name: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

export function getTerminalTheme() {
  return {
    background:    _cv('--hex-bg',     '#0d0b09'),
    foreground:    _cv('--hex-ink',    '#e8d5b0'),
    cursor:        _cv('--hex-amber',  '#c17f24'),
    cursorAccent:  _cv('--hex-bg',     '#0d0b09'),
    // ANSI 16 — semantic colors kept stable so the terminal reads correctly
    // in every theme; bg/fg/cursor/yellow track theme accents.
    black:         _cv('--hex-bg2',    '#1a1511'),
    red:           '#c0392b',
    green:         _cv('--hex-green',  '#7dae75'),
    yellow:        _cv('--hex-amber',  '#c17f24'),
    blue:          '#5a8a9f',
    magenta:       '#a05c7b',
    cyan:          _cv('--hex-cyan',   '#6aadaa'),
    white:         _cv('--hex-dim',    '#e8d5b0'),
    brightBlack:   _cv('--hex-faint',  '#4a3f35'),
    brightRed:     '#e74c3c',
    brightGreen:   _cv('--hex-green',  '#a8d5a2'),
    brightYellow:  _cv('--hex-copper', '#e09c34'),
    brightBlue:    '#7aacbf',
    brightMagenta: _cv('--hex-copper', '#c47b9b'),
    brightCyan:    _cv('--hex-cyan',   '#8acdca'),
    brightWhite:   _cv('--hex-ink',    '#f5ead6'),
  };
}

// Backwards-compatible static export (used by callers that don't re-theme).
// Prefer getTerminalTheme() for new code.
export const TERMINAL_THEME = getTerminalTheme();

// ─── Reconnect backoff ────────────────────────────────────────────────────────

export const BACKOFF_INIT_MS = 2_000;
export const BACKOFF_MAX_MS  = 30_000;
export const BACKOFF_MULT    = 2;

export function nextBackoff(prev: number): number {
  return Math.min(prev * BACKOFF_MULT, BACKOFF_MAX_MS);
}

// ─── Base64 stdin frame ───────────────────────────────────────────────────────

export function encodeInputFrame(text: string): string {
  // Pure helper: encode stdin text as a JSON WS frame with base64 data.
  const bytes = new TextEncoder().encode(text);
  let binary = '';
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  const b64 = btoa(binary);
  return JSON.stringify({ type: 'input', data: b64 });
}

// ─── Resize frame ─────────────────────────────────────────────────────────────

export function buildResizeFrame(cols: number, rows: number): string {
  // Pure helper: build a JSON resize frame.
  return JSON.stringify({ type: 'resize', cols, rows });
}

// ─── Fit cols/rows calculation ────────────────────────────────────────────────

/**
 * Calculate terminal dimensions from a pixel rect and font metrics.
 * Pure — no DOM dependency — so it can be unit-tested in Node.
 *
 * @returns {cols, rows} clamped to [1, 500] x [1, 200].
 */
export function calcFitDimensions(
  widthPx: number,
  heightPx: number,
  charW: number,
  charH: number,
): { cols: number; rows: number } {
  const cols = Math.max(1, Math.min(500, Math.floor(widthPx / charW)));
  const rows = Math.max(1, Math.min(200, Math.floor(heightPx / charH)));
  return { cols, rows };
}

// ─── Session-id helper ────────────────────────────────────────────────────────

/**
 * Build the next short session label from a 1-based index: PS 1, PS 2, ...
 * Pure so the tab-naming is unit-testable without a live panel.
 */
export function sessionLabel(index: number): string {
  return `PS ${index}`;
}
