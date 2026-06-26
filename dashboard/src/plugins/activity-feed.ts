/**
 * plugins/activity-feed.ts — Unified activity feed (replaces actions-log + git-activity).
 *
 * Merges signal types into a single scrollable list:
 *   [needs-you] — review tasks + open escalations, pinned at the TOP as
 *                 priority-flagged, clickable, actionable rows (P5 v-Next).
 *   [hive]      — operator actions, board events (cyan tag), chronological.
 *   [git]       — commits from /v1/git/activity (green tag), chronological.
 *
 * logAction() is the single sink for hive events (same API as actions-log was).
 * onGitActivity() ingests commit batches and deduplicates by hash.
 * onActivityNeedsYouBoard()/onActivityNeedsYouEscalations() fold the former
 * standalone "Needs You" rail in here (P5) — the panel is retired from the
 * barrel so there's no double-surfacing; the CC2 klaxon (alerts.ts) stays a
 * separate, independent path and is untouched by this module.
 *
 * All sinks are module-level so events captured before the panel mounts appear.
 */

import { register } from './registry.js';
import type { PanelPlugin, RelevanceResult } from './contract.js';
import type { SystemState, RenderBudget } from '../state/types.js';
import type { GitActivity, EscalationList } from '../types.js';
import type { BoardTask } from '../gateway.js';
import { moveBoardTask } from '../gateway.js';
import { escHtml } from '../format.js';
import { activateFullBoard } from './crew-board-full.js';
import { resolveSettings } from './instances.js';

export type ActionKind = 'alert' | 'done' | 'board' | 'action' | 'info';

// ─── Settings ─────────────────────────────────────────────────────────────────

interface ActivitySettings {
  /** Max chronological rows rendered. Default 200 == today's MAX cap. */
  maxRows: number;
  /** Render git commit rows. Default true == today's behavior. */
  showGit: boolean;
  /** Render hive action rows. Default true == today's behavior. */
  showHive: boolean;
}

/**
 * Defaults reproduce today's feed EXACTLY: render up to MAX (200) rows from
 * both sources. A fresh user sees no change.
 */
const DEFAULT_SETTINGS: ActivitySettings = {
  maxRows: 200,
  showGit: true,
  showHive: true,
};

interface ActivityEntry {
  ts: number;
  kind: ActionKind;
  label: string;
  source: 'action' | 'git';
  hash?: string;
}

const MAX = 200;
const _log: ActivityEntry[] = [];
let _rootEl: HTMLElement | null = null;

// ─── Needs-you state (folded in from needs-you.ts, P5) ────────────────────────

let _reviewTasks: BoardTask[] = [];
let _escalations: EscalationList['escalations'] = [];
let _needsRefresh: () => void = () => {};

/** Mirror of setNeedsYouRefresh: re-poll the board after an approve. */
export function setActivityNeedsYouRefresh(fn: () => void): void {
  _needsRefresh = fn;
}

/** Total pending needs-you items (escalations + review tasks). */
function _needsCount(): number {
  return _escalations.length + _reviewTasks.length;
}

// ─── Public sinks ─────────────────────────────────────────────────────────────

/** Append a hive action to the feed (newest first). Single public sink for hive events. */
export function logAction(kind: ActionKind, label: string, atMs?: number): void {
  if (!label) return;
  _log.unshift({ ts: atMs ?? Date.now(), kind, label: label.slice(0, 160), source: 'action' });
  if (_log.length > MAX) _log.length = MAX;
  _render();
}

/** Ingest a git activity batch. Deduplicates by hash; re-sorts descending by ts. */
export function onGitActivity(d: GitActivity | null): void {
  if (!d) return;
  const existingHashes = new Set(
    _log.filter((e) => e.source === 'git').map((e) => e.hash)
  );
  for (const c of d.commits) {
    if (existingHashes.has(c.hash)) continue;
    _log.push({
      ts: c.ts * 1000,
      kind: 'done',
      label: `${c.subject} [${c.project}]`,
      source: 'git',
      hash: c.hash,
    });
  }
  _log.sort((a, b) => b.ts - a.ts);
  if (_log.length > MAX) _log.length = MAX;
  _render();
}

/**
 * Fold the board's review tasks into the feed as priority needs-you rows.
 * Only `review`-status tasks become needs-you items (mirrors needs-you.ts).
 */
export function onActivityNeedsYouBoard(tasks: BoardTask[]): void {
  _reviewTasks = (tasks ?? []).filter((t) => t.status === 'review');
  _render();
}

/**
 * Fold open escalations into the feed as priority needs-you rows.
 * NOTE: this is the FEED surfacing only — the CC2 klaxon (trackEscalations in
 * alerts.ts) is a separate, independent path fed at the same call site.
 */
export function onActivityNeedsYouEscalations(list: EscalationList | null): void {
  _escalations = list?.escalations ?? [];
  _render();
}

// ─── Relevance ────────────────────────────────────────────────────────────────

function relevance(state: SystemState): RelevanceResult {
  if (state.tier === 'gaming') return { priority: 0, size: 'hidden' };
  // When something needs the operator, surface like the old needs-you rail did:
  // escalations push it highest, reviews a notch below. Otherwise stay calm.
  if (_escalations.length > 0) return { priority: 78, size: 'lg' };
  if (_reviewTasks.length > 0) return { priority: 64, size: 'md' };
  return { priority: state.activity === 'building' ? 52 : 46, size: 'md' };
}

// ─── Render ──────────────────────────────────────────────────────────────────

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

/** Build the HTML for the priority needs-you rows pinned at the top of the feed. */
function _needsHtml(): string {
  const parts: string[] = [];

  // F3: escalation rows get red hazard stripe, review rows get amber hazard stripe
  for (const e of _escalations) {
    parts.push(`
      <div class="alog-row afeed-needs afeed-needs-esc fx3-hazard fx3-hazard-red" data-needs="esc">
        <span class="afeed-needs-tag afeed-needs-tag-esc">ESC</span>
        <div class="afeed-needs-info">
          <span class="afeed-needs-title">${escHtml(e.title || e.slug)}</span>
          <span class="afeed-needs-sub">${escHtml(e.reason || 'escalation open')}</span>
        </div>
      </div>`);
  }

  for (const t of _reviewTasks) {
    parts.push(`
      <div class="alog-row afeed-needs afeed-needs-review fx3-hazard" data-needs="review" data-slug="${escHtml(t.slug)}">
        <span class="afeed-needs-tag afeed-needs-tag-review">REVIEW</span>
        <div class="afeed-needs-info">
          <span class="afeed-needs-title">${escHtml(t.title)}</span>
          <span class="afeed-needs-sub">${escHtml(t.project_slug || '')} · ${escHtml(t.slug)}</span>
        </div>
        <button class="afeed-needs-approve fx3-approve-btn" data-slug="${escHtml(t.slug)}" title="Approve → done">✓</button>
      </div>`);
  }

  return parts.join('');
}

/** Build the HTML for the chronological action/git rows (settings-filtered). */
function _chronoHtml(s: ActivitySettings): string {
  const cap = s.maxRows > 0 ? s.maxRows : DEFAULT_SETTINGS.maxRows;
  return _log
    .filter((e) => (e.source === 'git' ? s.showGit : s.showHive))
    .slice(0, cap)
    .map((e) => {
      const srcClass = e.source === 'git' ? 'afeed-src-git' : 'afeed-src-action';
      const tag      = e.source === 'git' ? 'git' : 'hive';
      return `
        <div class="alog-row alog-${e.kind}">
          <span class="alog-time">${_fmtTime(e.ts)}</span>
          <span class="alog-icon">${KIND_ICON[e.kind]}</span>
          <span class="afeed-src-tag ${srcClass}">${tag}</span>
          <span class="alog-label">${escHtml(e.label)}</span>
        </div>`;
    })
    .join('');
}

/** Wire the approve buttons on the freshly-rendered needs-you rows. */
function _bindApprove(list: HTMLElement): void {
  list.querySelectorAll<HTMLElement>('.afeed-needs-approve').forEach((btn) => {
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
        _render();
        _needsRefresh();
      } else {
        btn.textContent = '✗';
      }
    });
  });
}

function _render(): void {
  if (!_rootEl) return;
  const list  = _rootEl.querySelector('#afeed-list')  as HTMLElement | null;
  const count = _rootEl.querySelector('#afeed-count') as HTMLElement | null;
  if (!list) return;

  const total = _needsCount() + _log.length;
  if (count) count.textContent = total ? String(total) : '';

  if (total === 0) {
    list.innerHTML = '<p class="offline-state">No activity yet.</p>';
    return;
  }

  // Needs-you rows are pinned FIRST (priority), then the chronological feed
  // (filtered + capped per the instance settings; defaults == today's behavior).
  const settings = resolveSettings(_rootEl, 'activity-feed', DEFAULT_SETTINGS);
  list.innerHTML = _needsHtml() + _chronoHtml(settings);
  _bindApprove(list);
}

// ─── Mount / update ───────────────────────────────────────────────────────────

function mount(el: HTMLElement): void {
  _rootEl = el;
  el.innerHTML = `
    <div class="panel-header">
      <span class="panel-label">ACTIVITY</span>
      <span class="alog-count" id="afeed-count"></span>
    </div>
    <div id="afeed-list" class="alog-list"></div>
  `;
  // Click a needs-you row (not the approve button) → open the full board.
  el.addEventListener('click', (e) => {
    const t = e.target as HTMLElement | null;
    if (!t || t.closest('.afeed-needs-approve')) return;
    if (t.closest('.afeed-needs')) activateFullBoard();
  });
  _render();
}

function update(_state: SystemState, _budget: RenderBudget): void {
  // Driven by the sinks above; nothing per-tick.
}

// ─── Plugin definition ────────────────────────────────────────────────────────

const activityFeedPlugin: PanelPlugin = {
  id:          'activity-feed',
  title:       'ACTIVITY',
  dataSources: [{ kind: 'state' }],
  relevance,
  mount,
  update,
  defaultSettings: { ...DEFAULT_SETTINGS },
  settingsSchema: {
    fields: [
      {
        key: 'maxRows',
        label: 'Max rows',
        type: 'number',
        default: 200,
        hint: 'How many chronological events to show',
      },
      { key: 'showGit', label: 'Show git commits', type: 'boolean', default: true },
      { key: 'showHive', label: 'Show hive actions', type: 'boolean', default: true },
    ],
  },
};

register(activityFeedPlugin);
export { activityFeedPlugin };
