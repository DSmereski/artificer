/**
 * plugins/kpi.ts — status hero zone (P1 of the v3 redesign, "bigger stat area").
 *
 * Replaces the flat nine-tile KPI strip (the hero-metric cliché) with a denser,
 * three-zone command readout that fills the top band:
 *
 *   ┌── PULSE ──┬──────── ACTIVE LANES ────────┬── HEADLINE ──┐
 *   │ BUILDING  │ proj · now-doing · turns ·▮▮ │ done  spend  │
 *   │ vitals…   │ proj · now-doing · turns ·▮  │ ready hive/s │
 *   └───────────┴──────────────────────────────┴──────────────┘
 *
 * PULSE   — one synthesized swarm state (OFFLINE/GAMING/BUILDING/NEEDS YOU/IDLE)
 *           plus vital chips (gateway, GPU max, parse-fail, contention).
 * LANES   — a live row per building task: project, now-doing, turn count, a
 *           progress bar, and a stall warning. This is the genuinely-new info
 *           the old strip lacked: what each lane is doing right now.
 * HEADLINE— the few numbers that still earn a big read (done, spend, ready,
 *           hive tok/s), each with its sparkline.
 *
 * Pure SystemState read — no new data source. Clicking it jumps to the board.
 */

import { register } from './registry.js';
import type { PanelPlugin, RelevanceResult } from './contract.js';
import type { SystemState, RenderBudget, TaskProgress } from '../state/types.js';
import { fmtNum, fmtCost } from '../format.js';
import { drawSparkline, pushHist } from '../charts/sparkline.js';
import { activateFullBoard } from './crew-board-full.js';

let _rootEl: HTMLElement | null = null;
const _hist: Record<string, number[]> = {};
const _prevRaw: Record<string, number> = {};

// Resolve accent colors for canvas strokes from CSS vars at draw-time.
// Canvas can't read CSS vars directly — we resolve them via getComputedStyle.
function _resolveStroke(cssVar: string): string {
  const hex = getComputedStyle(document.documentElement).getPropertyValue(
    cssVar.replace('var(', '').replace(')', '')
  ).trim();
  return hex || '#c07840';
}

const STALL_MS = 90_000; // a lane with no update for this long reads as stalled

// ─── Pulse: synthesize one swarm state ────────────────────────────────────────

interface Pulse {
  word: string;
  accent: string; // css var
  sub: string;
}

function _pulse(s: SystemState): Pulse {
  if (!s.gatewayUp) return { word: 'OFFLINE', accent: 'var(--red)', sub: 'gateway unreachable' };
  if (s.tier === 'gaming') return { word: 'GAMING', accent: 'var(--dim)', sub: '4080 in use — hive yielded' };
  const b = s.tasks.building.length;
  if (b > 0) return { word: 'BUILDING', accent: 'var(--amber)', sub: `${b} lane${b === 1 ? '' : 's'} active` };
  if (s.escalations.open > 0) {
    const r = s.escalations.topReason ? ` · ${s.escalations.topReason}` : '';
    return { word: 'NEEDS YOU', accent: 'var(--red)', sub: `${s.escalations.open} escalation${s.escalations.open === 1 ? '' : 's'}${r}` };
  }
  return { word: 'IDLE', accent: 'var(--green)', sub: 'all clear' };
}

function _gpuMax(s: SystemState): number {
  const tis = s.resources.gpus.filter((g) => g.is5060);
  return tis.length ? Math.max(...tis.map((g) => g.utilPct)) : 0;
}

// ─── Headline numbers ─────────────────────────────────────────────────────────

interface Head {
  key: string;
  label: string;
  value: string;
  raw: number;
  accent: string;
}

function _head(s: SystemState): Head[] {
  return [
    { key: 'done',  label: 'done',       value: fmtNum(s.tasks.done),         raw: s.tasks.done,          accent: 'var(--green)' },
    { key: 'spend', label: 'spend',      value: fmtCost(s.counts.costUsd),    raw: s.counts.costUsd,      accent: 'var(--amber)' },
    { key: 'ready', label: 'ready',      value: String(s.tasks.ready),        raw: s.tasks.ready,         accent: 'var(--copper)' },
    { key: 'hive',  label: 'hive tok/s', value: fmtNum(s.counts.tokRateHive), raw: s.counts.tokRateHive,  accent: 'var(--cyan)' },
  ];
}

// ─── HTML escape ──────────────────────────────────────────────────────────────

function _esc(t: string): string {
  return t.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string));
}

// ─── Lane row ─────────────────────────────────────────────────────────────────

function _lane(t: TaskProgress): string {
  const name = t.project ? `${t.project}` : t.title;
  const now = t.lastAction ? _esc(t.lastAction) : 'working…';
  const pct = Math.round(Math.max(0, Math.min(1, t.progress)) * 100);
  const stalled = t.stalledMs > STALL_MS;
  // F3: stalled lanes get the hazard stall-border; lane-bar fill gets gradient
  return `
    <div class="hero-lane${stalled ? ' is-stalled fx3-stall-border' : ''}">
      <div class="hero-lane-top">
        <span class="hero-lane-name" title="${_esc(t.title)}">${_esc(name)}</span>
        <span class="hero-lane-turns" title="agent turns">${t.turns}t</span>
        ${stalled ? '<span class="hero-lane-stall" title="no update recently">stalled</span>' : ''}
      </div>
      <div class="hero-lane-now">${now}</div>
      <div class="hero-lane-bar"><span class="fx3-lane-bar" style="width:${pct}%"></span></div>
    </div>`;
}

function _lanes(s: SystemState): string {
  const b = s.tasks.building;
  if (b.length === 0) {
    return `<div class="hero-lanes-empty">no active builds — ${s.tasks.ready} ready in queue</div>`;
  }
  // Cap at 4 visible lanes; note overflow rather than silently truncating.
  const shown = b.slice(0, 4).map(_lane).join('');
  const extra = b.length > 4 ? `<div class="hero-lanes-more">+${b.length - 4} more building</div>` : '';
  return shown + extra;
}

// ─── Vitals chips ─────────────────────────────────────────────────────────────

function _vitals(s: SystemState): string {
  const gpu = Math.round(_gpuMax(s));
  const pf = Math.round(s.counts.parseFailRate * 100);
  const chips: string[] = [];
  chips.push(`<span class="hero-vital" title="gateway link"><span class="hero-vital-dot" style="background:${s.gatewayUp ? 'var(--green)' : 'var(--red)'}"></span>gw</span>`);
  chips.push(`<span class="hero-vital${s.resources.contended ? ' is-warn' : ''}" title="max AI-GPU utilisation">gpu ${gpu}%</span>`);
  if (pf > 0) chips.push(`<span class="hero-vital${pf > 10 ? ' is-warn' : ''}" title="tool-call parse-fail rate">pf ${pf}%</span>`);
  if (s.resources.contended) chips.push(`<span class="hero-vital is-warn" title="GPU contended (util/temp/vram)">contended</span>`);
  return chips.join('');
}

// ─── Relevance ────────────────────────────────────────────────────────────────

function relevance(state: SystemState): RelevanceResult {
  if (state.tier === 'gaming') return { priority: 0, size: 'hidden' };
  return { priority: 85, size: 'lg' };
}

// ─── Mount / update ───────────────────────────────────────────────────────────

function mount(el: HTMLElement): void {
  _rootEl = el;
  el.innerHTML = `
    <div class="hero">
      <div class="hero-pulse" data-role="pulse"></div>
      <div class="hero-lanes-wrap" data-role="lanes"></div>
      <div class="hero-head" data-role="head"></div>
    </div>`;
  el.style.cursor = 'pointer';
  el.addEventListener('click', (e) => {
    // Lane clicks + headline clicks both jump to the board.
    if ((e.target as HTMLElement)?.closest('.hero-lane, .hero-head-tile, .hero-pulse')) {
      activateFullBoard();
    }
  });
}

function update(state: SystemState, _budget: RenderBudget): void {
  if (!_rootEl) return;
  const pulseEl = _rootEl.querySelector('[data-role="pulse"]') as HTMLElement | null;
  const lanesEl = _rootEl.querySelector('[data-role="lanes"]') as HTMLElement | null;
  const headEl = _rootEl.querySelector('[data-role="head"]') as HTMLElement | null;
  if (!pulseEl || !lanesEl || !headEl) return;

  // PULSE
  // F3: RGB-split chromatic glow on the pulse word; breathing variant for NEEDS YOU / OFFLINE
  const p = _pulse(state);
  const needsBreath = p.word === 'NEEDS YOU' || p.word === 'OFFLINE';
  pulseEl.innerHTML = `
    <div class="hero-pulse-word fx3-rgb-split${needsBreath ? ' fx3-rgb-breathe' : ''}" style="color:${p.accent}">${p.word}</div>
    <div class="hero-pulse-sub">${_esc(p.sub)}</div>
    <div class="hero-vitals">${_vitals(state)}</div>`;

  // LANES
  lanesEl.innerHTML = _lanes(state);

  // HEADLINE
  const heads = _head(state);
  for (const h of heads) pushHist(_hist[h.key] ??= [], h.raw);
  headEl.innerHTML = heads.map((h) => {
    const changed = _prevRaw[h.key] !== undefined && _prevRaw[h.key] !== h.raw;
    _prevRaw[h.key] = h.raw;
    const idle = h.raw === 0;
    const valStyle = idle ? 'color:var(--faint);opacity:.4' : `color:${h.accent}`;
    return `
      <div class="hero-head-tile${changed ? ' kpi-pop' : ''}">
        <div class="hero-head-value" style="${valStyle}">${h.value}</div>
        <div class="hero-head-label">${h.label}</div>
        <canvas class="hero-head-spark" data-key="${h.key}" width="72" height="16"></canvas>
      </div>`;
  }).join('');

  headEl.querySelectorAll<HTMLCanvasElement>('.hero-head-spark').forEach((cv) => {
    const h = heads.find((x) => x.key === cv.dataset['key']);
    if (!h) return;
    drawSparkline(cv, _hist[h.key] ?? [], { color: _resolveStroke(h.accent) });
  });
}

const kpiPlugin: PanelPlugin = {
  id:          'kpi',
  title:       'STATUS',
  dataSources: [{ kind: 'state' }],
  relevance,
  mount,
  update,
};

register(kpiPlugin);
export { kpiPlugin };
