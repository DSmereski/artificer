/**
 * panels/right.ts — Right column: knowledge-graph placeholder + escalations + agenda.
 *
 * Phase B builds:
 *   - Sized placeholder for the d3-force graph (Phase C)
 *   - Escalation list from /v1/escalations (stub if no token)
 *   - Calendar agenda stub from /v1/calendar/jobs/upcoming
 *
 * All panels render graceful empty/offline states when data is unavailable.
 */

import type { EscalationList, CalendarJob } from '../types.js';
import { fmtTime, fmtRelative, escHtml } from '../format.js';

// ─── Graph placeholder ────────────────────────────────────────────────────────

export function initGraphPlaceholder(): void {
  const el = document.getElementById('graph-placeholder');
  if (!el) return;

  el.innerHTML = `
    <div class="graph-ph-inner">
      <span class="graph-ph-glyph">⬡</span>
      <span class="graph-ph-label">Knowledge Graph</span>
      <span class="graph-ph-sub">Phase C — d3-force canvas viz</span>
    </div>
  `;
}

// ─── Escalations ──────────────────────────────────────────────────────────────

export function updateEscalationsPanel(data: EscalationList | null): void {
  const panel = document.getElementById('escalations-panel');
  const badge = document.getElementById('esc-badge');

  if (badge) {
    const count = data?.open_count ?? 0;
    badge.textContent  = count > 0 ? String(count) : '';
    badge.className    = `esc-badge${count > 0 ? ' active' : ''}`;
  }

  if (!panel) return;

  if (!data) {
    panel.innerHTML = '<p class="offline-state">Auth needed for escalations.</p>';
    return;
  }

  if (data.escalations.length === 0) {
    panel.innerHTML = '<p class="offline-state">No open escalations. ✓</p>';
    return;
  }

  panel.innerHTML = data.escalations.slice(0, 6).map((esc) => `
    <div class="esc-row">
      <span class="esc-dot"></span>
      <div class="esc-body">
        <span class="esc-title">${escHtml(esc.title ?? esc.slug)}</span>
        <span class="esc-meta">${escHtml(esc.slug)} · ${fmtRelative(esc.created_at)}</span>
      </div>
    </div>
  `).join('');
}

// ─── Calendar agenda ──────────────────────────────────────────────────────────

export function updateAgendaPanel(jobs: CalendarJob[] | null): void {
  const panel = document.getElementById('agenda-panel');
  if (!panel) return;

  if (!jobs) {
    panel.innerHTML = '<p class="offline-state">Auth needed for calendar.</p>';
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
