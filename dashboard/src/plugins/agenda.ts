/**
 * plugins/agenda.ts — Calendar/agenda PanelPlugin.
 *
 * sm always; md if a job is due within 1 hour.
 * Wraps panels/right.ts updateAgendaPanel.
 */

import { register } from './registry.js';
import type { PanelPlugin, RelevanceResult } from './contract.js';
import type { SystemState, RenderBudget } from '../state/types.js';
import { updateAgendaPanel } from '../panels/right.js';
import type { CalendarJob } from '../types.js';
import { fmtTime, escHtml } from '../format.js';

// ─── Relevance ────────────────────────────────────────────────────────────────

function relevance(_state: SystemState): RelevanceResult {
  // Could check for upcoming jobs, but we rely on state for now
  return { priority: 40, size: 'sm' };
}

// ─── Mount ────────────────────────────────────────────────────────────────────

function mount(el: HTMLElement): void {
  el.innerHTML = `
    <div class="panel-header">
      <span class="panel-label">AGENDA</span>
    </div>
    <div id="v2-agenda-panel" class="agenda-panel">
      <div class="cfg-chip"><span class="cfg-dot"></span><span class="cfg-label">Calendar</span><span class="cfg-hint">connect Google Calendar</span></div>
    </div>
  `;
}

// ─── Update ───────────────────────────────────────────────────────────────────

function update(_state: SystemState, _budget: RenderBudget): void {
  // Agenda content is driven by direct poll callbacks, not just SystemState
  // The panel shows last-known data from onAgendaData()
}

/** Called by the poll adapter when new calendar data arrives. */
export function onAgendaData(jobs: CalendarJob[] | null): void {
  const panel = document.querySelector('#v2-agenda-panel') as HTMLElement | null;
  if (!panel) return;
  updateAgendaPanel.call(null, jobs);

  // Redirect updateAgendaPanel output to our panel
  if (!jobs) {
    panel.innerHTML = '<div class="cfg-chip"><span class="cfg-dot"></span><span class="cfg-label">Calendar</span><span class="cfg-hint">connect Google Calendar</span></div>';
    return;
  }
  if (jobs.length === 0) {
    panel.innerHTML = '<p class="offline-state">No upcoming jobs scheduled.</p>';
    return;
  }

  panel.innerHTML = jobs.slice(0, 6).map((job) => `
    <div class="agenda-row">
      <span class="agenda-time">${fmtTime(job.next_run)}</span>
      <div class="agenda-body">
        <span class="agenda-title">${escHtml(job.title)}</span>
        ${job.recurrence ? `<span class="agenda-chip">${escHtml(job.recurrence)}</span>` : ''}
      </div>
    </div>
  `).join('');
}

// ─── Plugin definition ────────────────────────────────────────────────────────

const agendaPlugin: PanelPlugin = {
  id:          'agenda',
  title:       'AGENDA',
  dataSources: [
    { kind: 'poll', endpoint: '/v1/calendar/jobs/upcoming', intervalKey: 'right' },
  ],
  relevance,
  mount,
  update,
};

register(agendaPlugin);
export { agendaPlugin };
