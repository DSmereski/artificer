/**
 * plugins/projects.ts — Projects management module.
 *
 * One panel to manage every project: see its live workload, toggle whether the
 * hive works it (On/Off), and one-click advance a FINISHED one — the Evolve
 * "continuous-dev" part: ✨ Suggest the next valuable work, ▶ Go do more to
 * build the top idea. Self-fetches /board/state; mouse-only.
 */

import { register } from './registry.js';
import {
  getBoardState, evolveSuggest, evolveGo, setProjectEnabled, proposePlan,
  type EvolveCandidate, type BoardState,
} from '../gateway.js';
import { cmdNewTask, cmdNewGoal } from '../topbar/commands.js';
import type { PanelPlugin, RelevanceResult } from './contract.js';
import type { SystemState, RenderBudget } from '../state/types.js';

const LIVE = new Set(['proposed', 'backlog', 'ready', 'in_progress', 'qa', 'review']);

interface Row { slug: string; active: number; total: number; enabled: boolean; }

let _root: HTMLElement | null = null;
let _timer: ReturnType<typeof setInterval> | null = null;
let _suspended = false;
let _stylesDone = false;
let _lastRows: Row[] = [];
const _sugg = new Map<string, EvolveCandidate[]>();   // last Suggest result per slug
const _msg = new Map<string, string>();               // last Go result per slug

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function _ensureStyles(): void {
  if (_stylesDone) return;
  _stylesDone = true;
  const css = document.createElement('style');
  css.textContent = `
  .pm-body { overflow-y:auto; height:100%; padding:2px 2px 8px; }
  .pm-row { display:flex; flex-direction:column; gap:4px; padding:7px 8px; border-bottom:1px solid var(--line,#222); }
  .pm-row:last-child { border-bottom:none; }
  .pm-top { display:flex; align-items:center; gap:8px; }
  .pm-name { flex:1; color:var(--txt,#ddd); font-size:13px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .pm-chip { font-size:10px; padding:1px 6px; border-radius:6px; white-space:nowrap; }
  .pm-chip.act  { background:color-mix(in oklch,var(--amber,#c08040) 16%,transparent); color:var(--amber,#c08040); }
  .pm-chip.done { background:color-mix(in oklch,var(--green,#5cc870) 14%,transparent); color:var(--green,#5cc870); }
  .pm-chip.idle { background:var(--card-hi,#222); color:var(--faint,#777); }
  .pm-btn { background:transparent; border:1px solid var(--line,#444); border-radius:5px; color:var(--txt-dim,#aaa); cursor:pointer; font-size:11px; padding:3px 8px; white-space:nowrap; font-family:var(--font-mono,monospace); }
  .pm-btn:hover { border-color:var(--accent,#c08040); color:var(--accent,#c08040); }
  .pm-btn.go  { background:var(--accent,#c08040); color:var(--on-amber,#1a1206); border-color:var(--accent,#c08040); font-weight:600; }
  .pm-btn.off { color:var(--faint,#777); }
  .pm-out  { font-size:11px; color:var(--txt-dim,#aaa); }
  .pm-cand { padding:3px 0; border-top:1px solid var(--line,#222); }
  .pm-score { color:var(--accent,#c08040); }
  .pm-src { color:var(--faint,#777); }
  .pm-why { color:var(--faint,#888); }
  .pm-ok  { color:var(--green,#5cc870); }
  .pm-empty { color:var(--faint,#777); padding:10px; font-size:12px; }
  `;
  document.head.appendChild(css);
}

function _rows(st: BoardState): Row[] {
  const active = new Map<string, number>();
  const total = new Map<string, number>();
  for (const t of st.tasks) {
    const s = (t as { project_slug: string }).project_slug;
    total.set(s, (total.get(s) || 0) + 1);
    if (LIVE.has((t as { status: string }).status)) active.set(s, (active.get(s) || 0) + 1);
  }
  return (st.projects || [])
    .map((p) => {
      const slug = (p as { slug: string }).slug;
      return {
        slug,
        active: active.get(slug) || 0,
        total: total.get(slug) || 0,
        enabled: !!(p as { enabled?: boolean }).enabled,
      };
    })
    .sort((a, b) =>
      Number(b.enabled) - Number(a.enabled) ||
      b.active - a.active ||
      a.slug.localeCompare(b.slug));
}

function _candHtml(c: EvolveCandidate, slug: string): string {
  return `<div class="pm-cand"><div><span class="pm-score">${(c.score ?? 0).toFixed(2)}</span> ${esc(c.title)} <span class="pm-src">${esc((c.source || []).join('+'))}</span></div><div class="pm-why">${esc(c.rationale || '')}</div><div style="margin-top:2px"><button class="pm-btn" data-act="toproposed" data-slug="${slug}" data-goal="${esc(c.title)}" title="draft a master plan for this → Proposed (approve it there)">→ Proposed</button></div></div>`;
}

/** Cached Suggest/Go output for a project, re-rendered on every refresh so it
 *  doesn't vanish on the 20s poll. */
function _outHtml(slug: string): string {
  const m = _msg.get(slug);
  if (m) return `<span class="pm-ok">${esc(m)}</span>`;
  const cs = _sugg.get(slug);
  if (cs && cs.length) return cs.map((c) => _candHtml(c, slug)).join('');
  return '';
}

function _rowHtml(r: Row): string {
  const chip = r.active > 0
    ? `<span class="pm-chip act">${r.active} active</span>`
    : r.total > 0
      ? `<span class="pm-chip done">done</span>`
      : `<span class="pm-chip idle">idle</span>`;
  // Evolve only makes sense for an enabled project with no live work.
  const evolveBtns = (r.enabled && r.active === 0)
    ? `<button class="pm-btn" data-act="suggest" data-slug="${r.slug}" title="analyze what's next">✨ Suggest</button>
       <button class="pm-btn go" data-act="go" data-slug="${r.slug}" title="build the top next step">▶ Go do more</button>`
    : '';
  return `<div class="pm-row" data-slug="${r.slug}">
    <div class="pm-top">
      <span class="pm-name" title="${esc(r.slug)}">${esc(r.slug)}</span>
      ${chip}
      <button class="pm-btn ${r.enabled ? '' : 'off'}" data-act="toggle" data-slug="${r.slug}" title="${r.enabled ? 'hive works this' : 'hive ignores this'}">${r.enabled ? 'On' : 'Off'}</button>
      ${evolveBtns}
    </div>
    <div class="pm-out" id="pm-out-${r.slug}">${_outHtml(r.slug)}</div>
  </div>`;
}

function _render(rows: Row[]): void {
  if (!_root) return;
  _lastRows = rows;
  const body = _root.querySelector('.pm-body');
  if (body) body.innerHTML = rows.length ? rows.map(_rowHtml).join('') : `<div class="pm-empty">No projects yet.</div>`;
  const head = _root.querySelector('.pm-count');
  if (head) head.textContent = String(rows.length);
}

async function _refresh(): Promise<void> {
  if (!_root || _suspended) return;
  try { _render(_rows(await getBoardState())); } catch { /* keep last render */ }
}

async function _suggest(slug: string): Promise<void> {
  const out = document.getElementById('pm-out-' + slug);
  if (out) out.textContent = 'Analyzing…';
  try {
    const cands: EvolveCandidate[] = await evolveSuggest(slug);
    _sugg.set(slug, cands); _msg.delete(slug);   // persist across the 20s refresh
    if (out) out.innerHTML = cands.length ? cands.map((c) => _candHtml(c, slug)).join('') : 'No candidates.';
  } catch (e) { if (out) out.textContent = 'Failed: ' + (e as Error).message; }
}

async function _go(slug: string): Promise<void> {
  const out = document.getElementById('pm-out-' + slug);
  if (out) out.textContent = 'Building the next step…';
  try {
    const d = await evolveGo(slug);
    const m = `Queued: ${d.evolved_from || 'next goal'} → ${d.created} tickets — track them on the crew board.`;
    _msg.set(slug, m); _sugg.delete(slug);   // persist across refresh
    if (out) out.innerHTML = `<span class="pm-ok">${esc(m)}</span>`;
    void _refresh();   // now has active work → chip flips, evolve buttons hide
  } catch (e) { if (out) out.textContent = 'Failed: ' + (e as Error).message; }
}

async function _toProposed(slug: string, goal: string, btn: HTMLButtonElement): Promise<void> {
  if (!goal) return;
  const prev = btn.textContent;
  btn.textContent = 'drafting…'; btn.disabled = true;
  try {
    const d = await proposePlan(slug, goal);
    btn.textContent = `✓ in Proposed (${d.steps})`;
  } catch (e) {
    btn.textContent = prev || '→ Proposed'; btn.disabled = false;
    alert('plan draft failed: ' + (e as Error).message);
  }
}

async function _toggle(slug: string): Promise<void> {
  const cur = _lastRows.find((r) => r.slug === slug);
  try { await setProjectEnabled(slug, !cur?.enabled); } catch { /* ignore */ }
  void _refresh();
}

function mount(el: HTMLElement): void {
  _ensureStyles();
  _root = el;
  el.innerHTML = `
    <div class="panel-header"><span class="panel-label">PROJECTS</span>
      <div class="panel-actions" id="pm-actions">
        <button class="panel-act-btn" data-act="task" title="New task">＋ Task</button>
        <button class="panel-act-btn" data-act="goal" title="Decompose a goal">✦ Goal</button>
      </div>
      <span class="pm-count" style="margin-left:8px;color:var(--faint,#777);font-size:11px"></span></div>
    <div class="pm-body"><div class="pm-empty">Loading…</div></div>`;
  el.addEventListener('click', (ev) => {
    const head = (ev.target as HTMLElement).closest<HTMLButtonElement>('.panel-act-btn');
    if (head) {
      if (head.dataset['act'] === 'task') cmdNewTask();
      else if (head.dataset['act'] === 'goal') cmdNewGoal();
      return;
    }
    const btn = (ev.target as HTMLElement).closest<HTMLButtonElement>('.pm-btn');
    if (!btn) return;
    const slug = btn.dataset['slug']; const act = btn.dataset['act'];
    if (!slug) return;
    if (act === 'suggest') void _suggest(slug);
    else if (act === 'go') void _go(slug);
    else if (act === 'toggle') void _toggle(slug);
    else if (act === 'toproposed') void _toProposed(slug, btn.dataset['goal'] || '', btn);
  });
  void _refresh();
  _timer = setInterval(_refresh, 20_000);
}

function update(_state: SystemState, _budget: RenderBudget): void { /* self-refreshes on its own timer */ }
function suspend(): void { _suspended = true; if (_timer) { clearInterval(_timer); _timer = null; } }
function resume(): void { _suspended = false; if (!_timer) _timer = setInterval(_refresh, 20_000); void _refresh(); }
function relevance(_state: SystemState): RelevanceResult { return { priority: 45, size: 'lg' }; }

const projectsPlugin: PanelPlugin = {
  id: 'projects',
  title: 'Projects',
  dataSources: [{ kind: 'state' }],
  relevance, mount, update, suspend, resume,
};

register(projectsPlugin);
export { projectsPlugin };
