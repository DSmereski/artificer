/**
 * plugins/escalations.ts — Escalations PanelPlugin.
 *
 * Hidden when no escalations; hero (instant, no dwell) when open > 0.
 * Wraps panels/right.ts updateEscalationsPanel.
 */

import { register } from './registry.js';
import type { PanelPlugin, RelevanceResult } from './contract.js';
import type { SystemState, RenderBudget } from '../state/types.js';
import { escHtml } from '../format.js';

// ─── Relevance ────────────────────────────────────────────────────────────────

function relevance(state: SystemState): RelevanceResult {
  const open = state.escalations.open;
  if (open === 0) return { priority: 5, size: 'hidden' };
  // > 0: surface immediately (store dwell bypasses escalation transitions)
  return { priority: 100, size: 'hero' };
}

// ─── State ────────────────────────────────────────────────────────────────────

let _rootEl: HTMLElement | null = null;

// ─── Mount ────────────────────────────────────────────────────────────────────

function mount(el: HTMLElement): void {
  _rootEl = el;
  // F3: fx3-alarm-blob gives a drifting red radial blob backdrop when alarmed
  el.innerHTML = `
    <div class="panel-header escalation-header">
      <span class="panel-label">ESCALATIONS</span>
      <span class="esc-badge-v2" id="v2-esc-count"></span>
    </div>
    <div id="v2-escalations-panel" class="escalations-panel"></div>
  `;
}

// ─── Update ───────────────────────────────────────────────────────────────────

function update(state: SystemState, _budget: RenderBudget): void {
  if (!_rootEl) return;

  const countEl = _rootEl.querySelector('#v2-esc-count') as HTMLElement | null;
  const open = state.escalations.open;
  if (countEl) {
    countEl.textContent = open > 0 ? `▲${open}` : '';
    // F3: chrome-bevel alarm badge when active
    countEl.className   = `esc-badge-v2${open > 0 ? ' active fx3-badge-alarm' : ''}`;
  }

  // F3: red ambient blob on the root panel when alarmed
  if (open > 0) {
    _rootEl.classList.add('fx3-alarm-blob');
  } else {
    _rootEl.classList.remove('fx3-alarm-blob');
  }

  const panel = _rootEl.querySelector('#v2-escalations-panel') as HTMLElement | null;
  if (!panel) return;

  if (open === 0) {
    panel.innerHTML = '<p class="offline-state">No open escalations. ✓</p>';
    return;
  }

  // Show top reason if available
  // F3: RGB-split on the top-reason text for distress-signal feel
  const topReason = state.escalations.topReason;
  panel.innerHTML = `
    <div class="esc-summary">
      <span class="esc-open-count">${state.escalations.open} open</span>
      ${topReason ? `<span class="esc-top-reason fx3-rgb-split">${escHtml(topReason)}</span>` : ''}
    </div>
    <p class="offline-state esc-auth-note">Auth token required to view details.</p>
  `;
}

// ─── Plugin definition ────────────────────────────────────────────────────────

const escalationsPlugin: PanelPlugin = {
  id:          'escalations',
  title:       'ESCALATIONS',
  dataSources: [
    { kind: 'poll', endpoint: '/v1/escalations', intervalKey: 'right' },
    { kind: 'ws',   topic: 'v1events' },
  ],
  relevance,
  mount,
  update,
};

register(escalationsPlugin);
export { escalationsPlugin };
