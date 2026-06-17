/**
 * plugins/crew-board-full.ts — Full crew board, embedded via iframe.
 *
 * Approach C (hybrid): the always-on `crew-board` glance stays an adaptive
 * citizen; THIS plugin is hidden (priority 0) until activated, whereupon it
 * requests a `hero` cell and frames the gateway's complete `/board?embed=1`
 * page — the single, feature-complete source of truth (7 columns, detail
 * dialog, transcript, diff, decompose, stats, all 12 mutations). No
 * reimplementation, no duplicate maintenance.
 *
 * Activation: a click on the glance hero (crew-board.ts) calls
 * activateFullBoard(); Esc or the in-panel ← back button calls
 * deactivateFullBoard(). Each toggle nudges the store to force a re-layout.
 *
 * Governor / lifecycle (mirrors terminal.ts): the iframe is a second renderer
 * the dashboard governor can't see into, so we blank its `src` to about:blank
 * on suspend() and when the tier is gaming — that stops WebView2's nested
 * render/poll/WS. We restore it on resume / when leaving gaming.
 *
 * Auth: the embedded page injects its OWN _BOARD_TOKEN and sends it as
 * X-Board-Token on mutations. The dashboard never forwards its device Bearer
 * into the frame. Loopback-only: src is http://127.0.0.1:8766 in prod.
 */

import { register } from './registry.js';
import { boardEmbedUrl } from '../gateway.js';
import type { PanelPlugin, RelevanceResult } from './contract.js';
import type { SystemState, RenderBudget } from '../state/types.js';

// ─── Module state ───────────────────────────────────────────────────────────

// P2: the full board is now an ALWAYS-ON inline citizen seated in the layout's
// `board` slot — no longer an overlay toggled by _active. _active stays true so
// the legacy activate/deactivate API (kpi click, command palette) is harmless.
let _active = true;
let _suspended = false;
let _gaming = false;
let _iframe: HTMLIFrameElement | null = null;
let _nudge: (() => void) | null = null;

const BLANK = 'about:blank';

// ─── Wiring from main.ts ──────────────────────────────────────────────────────

/**
 * Register the store re-emit callback so a toggle (a user click, not a state
 * change) can force the layout engine to re-seat panels. Called once at init.
 */
export function setFullBoardNudge(fn: () => void): void {
  _nudge = fn;
}

// ─── Activation API (called by the glance + Esc handler) ──────────────────────

export function isFullBoardActive(): boolean {
  return _active;
}

export function activateFullBoard(): void {
  // Board is always inline now; nudge a re-layout in case a caller expects it.
  _nudge?.();
}

export function deactivateFullBoard(): void {
  // P2: the board is a permanent inline panel now — collapsing it back to a
  // glance no longer applies. Kept as a no-op so legacy callers (Esc handler,
  // command palette) don't blank the embedded kanban.
}

// ─── iframe src control ───────────────────────────────────────────────────────

function _loadFrame(): void {
  if (!_iframe) return;
  const target = boardEmbedUrl();
  if (_iframe.src !== target) _iframe.src = target;
}

function _blankFrame(): void {
  if (_iframe && _iframe.src !== BLANK) _iframe.src = BLANK;
}

/** True when the board should actively render (visible, not suspended/gaming). */
function _shouldRender(): boolean {
  return !_suspended && !_gaming;
}

// ─── Relevance ────────────────────────────────────────────────────────────────

function relevance(state: SystemState): RelevanceResult {
  // Never seat the heavy full board (nested iframe renderer) while gaming.
  if (state.tier === 'gaming') return { priority: 0, size: 'hidden' };
  // Always visible: the embedded kanban owns the layout's `board` slot.
  return { priority: 90, size: 'lg' };
}

// ─── Mount ────────────────────────────────────────────────────────────────────

function mount(el: HTMLElement): void {
  el.innerHTML = `
    <div class="panel-header">
      <span class="panel-label">CREW BOARD</span>
    </div>
    <div class="board-full-frame-wrap">
      <iframe
        id="v2-board-full-frame"
        class="board-full-frame"
        title="Crew Board"
        referrerpolicy="no-referrer"
      ></iframe>
    </div>
  `;

  _iframe = el.querySelector('#v2-board-full-frame');
  if (_shouldRender()) _loadFrame();
  else _blankFrame();
}

// ─── Update ───────────────────────────────────────────────────────────────────

function update(state: SystemState, _budget: RenderBudget): void {
  // Drop the nested iframe renderer while gaming; restore otherwise. The panel
  // is also hidden by relevance() in gaming, so this is belt-and-suspenders.
  _gaming = state.tier === 'gaming';
  if (_iframe) {
    if (_shouldRender()) _loadFrame();
    else _blankFrame();
  }
}

// ─── suspend / resume ─────────────────────────────────────────────────────────

function suspend(): void {
  _suspended = true;
  _blankFrame();
}

function resume(): void {
  _suspended = false;
  if (_shouldRender()) _loadFrame();
}

// ─── Plugin definition ────────────────────────────────────────────────────────

const crewBoardFullPlugin: PanelPlugin = {
  id:          'crew-board-full',
  title:       'CREW BOARD — FULL',
  dataSources: [{ kind: 'state' }], // the iframe self-fetches; no dashboard polls
  relevance,
  mount,
  update,
  suspend,
  resume,
};

register(crewBoardFullPlugin);
export { crewBoardFullPlugin };
