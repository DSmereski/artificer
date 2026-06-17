/**
 * plugins/needs-you.ts — "Needs you" rail (CC2).
 *
 * The one place to glance for "what wants me": tasks awaiting review approval,
 * open escalations, and stalled builds. Hidden when nothing needs attention;
 * surfaces high-priority the moment something does. Approve straight from the
 * rail (review → done) via the loopback X-Board-Token.
 *
 * Fed by bridges from the board / escalation polls (main.ts), mirroring the
 * suno feed pattern — keeps the panel out of the poll plumbing.
 */

import { register } from './registry.js';
import type { PanelPlugin, RelevanceResult } from './contract.js';
import type { SystemState, RenderBudget } from '../state/types.js';
import type { BoardTask } from '../gateway.js';
import type { EscalationList } from '../types.js';
import { moveBoardTask } from '../gateway.js';
import { escHtml } from '../format.js';
import { logAction } from './actions-log.js';
import { activateFullBoard } from './crew-board-full.js';

// ─── Cached feed (set by main.ts poll bridges) ────────────────────────────────

let _reviewTasks: BoardTask[] = [];
let _escalations: EscalationList['escalations'] = [];
let _rootEl: HTMLElement | null = null;
let _refresh: () => void = () => {};

export function setNeedsYouRefresh(fn: () => void): void {
  _refresh = fn;
}

export function onNeedsYouBoard(tasks: BoardTask[]): void {
  _reviewTasks = tasks.filter((t) => t.status === 'review');
  _rerender();
}

export function onNeedsYouEscalations(list: EscalationList | null): void {
  _escalations = list?.escalations ?? [];
  _rerender();
}

// ─── Stalled builds (from SystemState building set) ───────────────────────────

let _stalled = 0;
const STALL_MS = 300_000; // 5 min without progress

// ─── Relevance ────────────────────────────────────────────────────────────────

function _attentionCount(): number {
  return _reviewTasks.length + _escalations.length + _stalled;
}

function relevance(state: SystemState): RelevanceResult {
  _stalled = state.tasks.building.filter((t) => t.stalledMs > STALL_MS).length;
  if (state.tier === 'gaming') return { priority: 0, size: 'hidden' };
  if (_attentionCount() === 0) return { priority: 0, size: 'hidden' };
  // Above the calm panels, below a live escalation-hero. Escalations push it up.
  const priority = _escalations.length > 0 ? 78 : 64;
  return { priority, size: 'md' };
}

// ─── Mount / render ─────────────────────────────────────────────────────────────

function mount(el: HTMLElement): void {
  _rootEl = el;
  el.innerHTML = `
    <div class="panel-header">
      <span class="panel-label">NEEDS YOU</span>
      <span class="needs-count" id="needs-count"></span>
    </div>
    <div id="needs-list" class="needs-list"></div>
  `;
  // it9: click a row (not the approve button) → open the full board for context.
  el.addEventListener('click', (e) => {
    const t = e.target as HTMLElement | null;
    if (!t || t.closest('.needs-approve')) return;
    if (t.closest('.needs-row')) activateFullBoard();
  });
  _rerender();
}

function _rerender(): void {
  if (!_rootEl) return;
  const list = _rootEl.querySelector('#needs-list') as HTMLElement | null;
  const count = _rootEl.querySelector('#needs-count') as HTMLElement | null;
  if (!list) return;

  const n = _attentionCount();
  if (count) count.textContent = n > 0 ? String(n) : '';

  if (n === 0) {
    list.innerHTML = '<p class="offline-state">All clear. Nothing needs you.</p>';
    return;
  }

  const parts: string[] = [];

  for (const e of _escalations) {
    parts.push(`
      <div class="needs-row needs-esc">
        <span class="needs-tag needs-tag-esc">ESC</span>
        <div class="needs-info">
          <span class="needs-title">${escHtml(e.title || e.slug)}</span>
          <span class="needs-sub">${escHtml(e.reason || 'escalation open')}</span>
        </div>
      </div>`);
  }

  for (const t of _reviewTasks) {
    parts.push(`
      <div class="needs-row needs-review" data-slug="${escHtml(t.slug)}">
        <span class="needs-tag needs-tag-review">REVIEW</span>
        <div class="needs-info">
          <span class="needs-title">${escHtml(t.title)}</span>
          <span class="needs-sub">${escHtml(t.project_slug || '')} · ${escHtml(t.slug)}</span>
        </div>
        <button class="needs-approve" data-slug="${escHtml(t.slug)}" title="Approve → done">✓</button>
      </div>`);
  }

  list.innerHTML = parts.join('');

  list.querySelectorAll<HTMLElement>('.needs-approve').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const slug = btn.dataset['slug'];
      if (!slug) return;
      btn.textContent = '…';
      btn.setAttribute('disabled', 'true');
      const ok = await moveBoardTask(slug, 'done');
      if (ok) {
        logAction('action', `Approved ${slug} → done`);
        _reviewTasks = _reviewTasks.filter((t) => t.slug !== slug);
        _rerender();
        _refresh();
      } else {
        btn.textContent = '✗';
      }
    });
  });
}

function update(state: SystemState, _budget: RenderBudget): void {
  // Recompute stalled from the live building set; re-render if it changed.
  const stalled = state.tasks.building.filter((t) => t.stalledMs > STALL_MS).length;
  if (stalled !== _stalled) {
    _stalled = stalled;
    _rerender();
  }
}

// ─── Plugin definition ────────────────────────────────────────────────────────

const needsYouPlugin: PanelPlugin = {
  id:          'needs-you',
  title:       'NEEDS YOU',
  dataSources: [{ kind: 'state' }],
  relevance,
  mount,
  update,
};

register(needsYouPlugin);
export { needsYouPlugin };
