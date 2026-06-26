/**
 * plugins/wiki-review.ts — Wiki Review rail (C4).
 *
 * Surfaces open wiki review items (contradictions + gaps) detected by
 * wiki_synth.  Hidden when there are no open items; shows as a small
 * rail when items are present.
 *
 * Actions per row:
 *   ✓ Resolve   → POST /v1/wiki/reviews/{id}/resolve?status=resolved
 *   ✗ Dismiss   → POST /v1/wiki/reviews/{id}/resolve?status=dismissed
 *   🔍 Research → POST /v1/wiki/reviews/{id}/research?confirm=false
 *                 (shows topics without ingesting; confirm not triggered
 *                 from the dashboard — safety gate stays in the gateway)
 *
 * Theme-token driven: uses the same --hive-warn / --hive-accent tokens as
 * the escalations panel.
 *
 * Auth: uses getBearerToken() — same as graph.ts.  When the dashboard runs
 * without a token (wallpaper, no pair), the panel is hidden (priority 0).
 */

import { register } from './registry.js';
import type { PanelPlugin, RelevanceResult } from './contract.js';
import type { SystemState, RenderBudget } from '../state/types.js';
import { fetchV1 } from '../gateway.js';
import { getBearerToken } from '../gateway.js';
import { escHtml } from '../format.js';
import { logAction } from './actions-log.js';
import { resolveSettings } from './instances.js';

// ─── Settings ─────────────────────────────────────────────────────────────────

interface WikiReviewSettings {
  maxItems: number;
}

/**
 * Default 20 == today's behavior: the fetch caps at `limit=20` and the panel
 * rendered every returned row. A fresh user sees no change.
 */
const DEFAULT_SETTINGS: WikiReviewSettings = { maxItems: 20 };

// ─── Types ────────────────────────────────────────────────────────────────────

interface WikiReview {
  id: number;
  slug: string;
  kind: 'contradiction' | 'gap' | 'stale';
  summary: string;
  source_notes: string[];
  status: 'open';
  created_at: string;
}

interface WikiReviewsResponse {
  reviews: WikiReview[];
  count: number;
}

// ─── Module state ─────────────────────────────────────────────────────────────

let _reviews: WikiReview[] = [];
let _rootEl: HTMLElement | null = null;
let _lastFetch = 0;
const FETCH_INTERVAL_MS = 30_000;

// ─── Data fetch ───────────────────────────────────────────────────────────────

async function _fetchReviews(): Promise<void> {
  try {
    const data = await fetchV1<WikiReviewsResponse>('/wiki/reviews?limit=20');
    if (!data) return;
    _reviews = data.reviews ?? [];
    _lastFetch = Date.now();
    _rerender();
  } catch (err) {
    console.warn('[wiki-review] fetch failed', err);
  }
}

// ─── Relevance ────────────────────────────────────────────────────────────────

function relevance(state: SystemState): RelevanceResult {
  // Require a bearer token — vault routes are authenticated.
  if (!getBearerToken()) return { priority: 0, size: 'hidden' };
  if (state.tier === 'gaming') return { priority: 0, size: 'hidden' };

  if (_reviews.length === 0) return { priority: 0, size: 'hidden' };

  // Surface below escalations (78) and needs-you (64), above calm panels.
  const hasContradiction = _reviews.some((r) => r.kind === 'contradiction');
  return {
    priority: hasContradiction ? 55 : 40,
    size: 'sm',
  };
}

// ─── Mount ────────────────────────────────────────────────────────────────────

function mount(el: HTMLElement): void {
  _rootEl = el;
  el.innerHTML = `
    <div class="panel-header wiki-review-header">
      <span class="panel-label">WIKI REVIEW</span>
      <span class="wiki-review-badge" id="wiki-review-badge"></span>
    </div>
    <div id="wiki-review-list" class="wiki-review-list"></div>
  `;
  void _fetchReviews();
}

// ─── Actions ──────────────────────────────────────────────────────────────────

async function _postAction(id: number, action: 'resolve' | 'dismiss' | 'research'): Promise<boolean> {
  const token = getBearerToken();
  if (!token) return false;
  try {
    if (action === 'research') {
      // Research is confirm=false (preview only — ingest stays gateway-gated).
      const data = await fetchV1<{ ok: boolean; topics: string[]; skipped_reason?: string }>(
        `/wiki/reviews/${id}/research?confirm=false`,
      );
      if (!data) return false;
      const msg = data.ok
        ? `Research topics: ${(data.topics ?? []).join(', ')}`
        : (data.skipped_reason ?? 'research skipped');
      logAction('info', `[wiki] research #${id}: ${msg}`);
      return true;
    }

    const status = action === 'resolve' ? 'resolved' : 'dismissed';
    const res = await fetch(
      `http://127.0.0.1:8766/v1/wiki/reviews/${id}/resolve?status=${status}`,
      {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      },
    );
    if (!res.ok) return false;
    logAction('action', `[wiki] ${action} review #${id}`);
    _reviews = _reviews.filter((r) => r.id !== id);
    _rerender();
    return true;
  } catch (err) {
    console.warn('[wiki-review] action failed', err);
    return false;
  }
}

// ─── Render ───────────────────────────────────────────────────────────────────

function _kindLabel(kind: string): string {
  if (kind === 'contradiction') return '⚠ CONFLICT';
  if (kind === 'gap') return '○ GAP';
  return '~ STALE';
}

function _kindClass(kind: string): string {
  if (kind === 'contradiction') return 'wiki-kind-conflict';
  if (kind === 'gap') return 'wiki-kind-gap';
  return 'wiki-kind-stale';
}

function _rerender(): void {
  if (!_rootEl) return;

  const badge = _rootEl.querySelector('#wiki-review-badge') as HTMLElement | null;
  const list = _rootEl.querySelector('#wiki-review-list') as HTMLElement | null;
  if (!list) return;

  const n = _reviews.length;
  if (badge) {
    badge.textContent = n > 0 ? String(n) : '';
    badge.className = `wiki-review-badge${n > 0 ? ' active' : ''}`;
  }

  if (n === 0) {
    list.innerHTML = '<p class="offline-state">All wiki reviews clear. ✓</p>';
    return;
  }

  const { maxItems } = resolveSettings(_rootEl, 'wiki-review', DEFAULT_SETTINGS);
  const cap = maxItems > 0 ? maxItems : DEFAULT_SETTINGS.maxItems;

  // F3: contradiction rows get red hazard stripe; gap rows get inset-sheen left accent;
  //     kind-tags get clip-path notch; action buttons get per-action hover glow
  const parts: string[] = _reviews.slice(0, cap).map((r) => {
    const isConflict = r.kind === 'contradiction';
    const rowExtra = isConflict ? ' fx3-hazard fx3-hazard-red' : '';
    return `
    <div class="wiki-review-row${rowExtra}" data-id="${r.id}">
      <span class="wiki-kind-tag ${escHtml(_kindClass(r.kind))} fx3-kind-notch">${escHtml(_kindLabel(r.kind))}</span>
      <div class="wiki-review-info">
        <span class="wiki-review-slug">${escHtml(r.slug)}</span>
        <span class="wiki-review-summary">${escHtml(r.summary)}</span>
      </div>
      <div class="wiki-review-actions">
        <button class="wiki-btn wiki-resolve fx3-wiki-resolve" data-id="${r.id}" title="Resolve">✓</button>
        <button class="wiki-btn wiki-dismiss fx3-wiki-dismiss" data-id="${r.id}" title="Dismiss">✗</button>
        ${r.kind === 'gap' ? `<button class="wiki-btn wiki-research fx3-wiki-research" data-id="${r.id}" title="Research (preview)">🔍</button>` : ''}
      </div>
    </div>
  `;
  });

  list.innerHTML = parts.join('');

  list.querySelectorAll<HTMLElement>('.wiki-resolve').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const id = Number(btn.dataset['id']);
      btn.setAttribute('disabled', 'true');
      btn.textContent = '…';
      await _postAction(id, 'resolve');
    });
  });

  list.querySelectorAll<HTMLElement>('.wiki-dismiss').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const id = Number(btn.dataset['id']);
      btn.setAttribute('disabled', 'true');
      btn.textContent = '…';
      await _postAction(id, 'dismiss');
    });
  });

  list.querySelectorAll<HTMLElement>('.wiki-research').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const id = Number(btn.dataset['id']);
      btn.setAttribute('disabled', 'true');
      btn.textContent = '…';
      const ok = await _postAction(id, 'research');
      btn.removeAttribute('disabled');
      btn.textContent = ok ? '🔍' : '!';
    });
  });
}

// ─── Update (state tick) ──────────────────────────────────────────────────────

function update(_state: SystemState, _budget: RenderBudget): void {
  // Refresh once per interval if we have a token.
  if (getBearerToken() && Date.now() - _lastFetch > FETCH_INTERVAL_MS) {
    void _fetchReviews();
  }
}

// ─── suspend / resume ─────────────────────────────────────────────────────────

function suspend(): void {
  // Nothing to freeze — fetch is one-shot, driven by update().
}

function resume(): void {
  // Force a re-fetch when the panel becomes visible again.
  _lastFetch = 0;
}

// ─── Plugin definition ────────────────────────────────────────────────────────

const wikiReviewPlugin: PanelPlugin = {
  id:          'wiki-review',
  title:       'WIKI REVIEW',
  dataSources: [{ kind: 'state' }],
  relevance,
  mount,
  update,
  suspend,
  resume,
  defaultSettings: { ...DEFAULT_SETTINGS },
  settingsSchema: {
    fields: [
      {
        key: 'maxItems',
        label: 'Max rows',
        type: 'number',
        default: 20,
        hint: 'How many wiki review items to list',
      },
    ],
  },
};

register(wikiReviewPlugin);
export { wikiReviewPlugin };
