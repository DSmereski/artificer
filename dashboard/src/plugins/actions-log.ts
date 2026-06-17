/**
 * plugins/actions-log.ts — live Actions Log (DASH it1).
 *
 * A rolling, timestamped feed of everything happening across the estate:
 * board task moves, escalations, QA/review/done transitions, gateway WS
 * events, and the operator's own dashboard actions (pause, approve, new task).
 * Module-level ring buffer so events are captured even before the panel mounts.
 *
 * logAction() is the single sink — main.ts feeds it from the WS handlers and
 * the command surface feeds it on each action.
 */

import { register } from './registry.js';
import type { PanelPlugin, RelevanceResult } from './contract.js';
import type { SystemState, RenderBudget } from '../state/types.js';
import { escHtml } from '../format.js';

export type ActionKind = 'alert' | 'done' | 'board' | 'action' | 'info';

interface ActionEntry {
  ts: number;       // epoch ms
  kind: ActionKind;
  label: string;
}

const MAX = 200;
const _log: ActionEntry[] = [];
let _rootEl: HTMLElement | null = null;
let _seq = 0;

/** Append an action to the log (newest first). The single public sink. */
export function logAction(kind: ActionKind, label: string, atMs?: number): void {
  if (!label) return;
  _log.unshift({ ts: atMs ?? _now(), kind, label: label.slice(0, 160) });
  if (_log.length > MAX) _log.length = MAX;
  _seq++;
  _render();
}

function _now(): number {
  // Browser context — Date.now() is fine here (unlike workflow sandboxes).
  return Date.now();
}

// ─── Relevance ────────────────────────────────────────────────────────────────

function relevance(state: SystemState): RelevanceResult {
  if (state.tier === 'gaming') return { priority: 0, size: 'hidden' };
  // Always-on operator feed; modest size, rises a touch when busy.
  return { priority: state.activity === 'building' ? 52 : 46, size: 'md' };
}

// ─── Render ─────────────────────────────────────────────────────────────────────

const KIND_ICON: Record<ActionKind, string> = {
  alert:  '▲',
  done:   '✓',
  board:  '◆',
  action: '➤',
  info:   '·',
};

function _fmtTime(ts: number): string {
  const d = new Date(ts);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function mount(el: HTMLElement): void {
  _rootEl = el;
  el.innerHTML = `
    <div class="panel-header">
      <span class="panel-label">ACTIONS LOG</span>
      <span class="alog-count" id="alog-count"></span>
    </div>
    <div id="alog-list" class="alog-list"></div>
  `;
  _render();
}

function _render(): void {
  if (!_rootEl) return;
  const list = _rootEl.querySelector('#alog-list') as HTMLElement | null;
  const count = _rootEl.querySelector('#alog-count') as HTMLElement | null;
  if (!list) return;
  if (count) count.textContent = _log.length ? String(_log.length) : '';

  if (_log.length === 0) {
    list.innerHTML = '<p class="offline-state">No activity yet.</p>';
    return;
  }

  list.innerHTML = _log.map((e) => `
    <div class="alog-row alog-${e.kind}">
      <span class="alog-time">${_fmtTime(e.ts)}</span>
      <span class="alog-icon">${KIND_ICON[e.kind]}</span>
      <span class="alog-label">${escHtml(e.label)}</span>
    </div>
  `).join('');
}

function update(_state: SystemState, _budget: RenderBudget): void {
  // Driven by logAction(); nothing per-tick.
}

// ─── Plugin definition ────────────────────────────────────────────────────────

const actionsLogPlugin: PanelPlugin = {
  id:          'actions-log',
  title:       'ACTIONS LOG',
  dataSources: [{ kind: 'state' }],
  relevance,
  mount,
  update,
};

register(actionsLogPlugin);
export { actionsLogPlugin };
