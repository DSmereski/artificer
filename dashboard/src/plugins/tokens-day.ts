/**
 * plugins/tokens-day.ts — Tokens-per-day uPlot line chart panel (v-Next P4).
 *
 * Fetches /board/tokens-by-day?days=<n> (open, no auth) and renders a two-series
 * uPlot line chart: hive tokens (green) and claude tokens (cyan).
 * Copper/amber dark theme to match the dashboard aesthetic.
 *
 * Per-instance settings (drive the gear form via `settingsSchema`):
 *   - range (select) — '7d' | '30d' | '90d' (default '30d')
 *     The endpoint is day-granular, so day-based ranges are the only
 *     meaningful windows (1h/6h on a per-day chart would be nonsense). The
 *     range maps directly to the `days` query param via `rangeToDays()`.
 *
 * Multi-instance: settings are read per cell from the `data-instance-id`
 * attribute the layout sets on the cell element before mount() (see
 * layout/freeform-apply.ts). Two tokens-day instances with different ranges
 * resolve different instanceIds → different windows. Per-cell view state is
 * kept in a WeakMap keyed by the cell element so the two never clobber each
 * other. (Same convention as plugins/weather.ts.)
 *
 * An inline <select> in the panel header lets the user change the range
 * directly on the panel — it writes the SAME instance settings as the gear
 * form (updateInstanceSettings), so the two stay in sync.
 *
 * Back-compat: a legacy single default instance (no instance row) falls back to
 * DEFAULT_SETTINGS.range ('30d') → 30 days, exactly today's behavior.
 *
 * Activates once the gateway endpoint is available; shows an empty/loading
 * state until then.
 */

import uPlot from 'uplot';
import { register } from './registry.js';
import type { PanelPlugin, RelevanceResult, Rect } from './contract.js';
import type { SystemState, RenderBudget } from '../state/types.js';
import { tokensByDay } from '../gateway.js';
import type { TokensByDayEntry } from '../gateway.js';
import { getInstance, updateInstanceSettings } from './instances.js';
import { escHtml } from '../format.js';

// ─── Settings types ───────────────────────────────────────────────────────────

export type TokensRange = '7d' | '30d' | '90d';

interface TokensSettings {
  range: TokensRange;
}

const DEFAULT_SETTINGS: TokensSettings = {
  range: '30d',
};

/** Range select options — day-granular windows (endpoint is per-day). */
const RANGE_OPTIONS: ReadonlyArray<{ value: TokensRange; label: string }> = [
  { value: '7d', label: '7 days' },
  { value: '30d', label: '30 days' },
  { value: '90d', label: '90 days' },
];

/**
 * Pure mapping: range value → `days` query param for /board/tokens-by-day.
 * Safe default is 30 (today's behavior) for any unrecognized input.
 * Exported for testing.
 */
export function rangeToDays(range: string): number {
  switch (range) {
    case '7d':
      return 7;
    case '30d':
      return 30;
    case '90d':
      return 90;
    default:
      return 30; // safe default == legacy behavior
  }
}

/** Coerce an arbitrary value to a valid TokensRange (falls back to default). */
function _coerceRange(raw: unknown): TokensRange {
  return raw === '7d' || raw === '30d' || raw === '90d' ? raw : DEFAULT_SETTINGS.range;
}

/**
 * Read the merged settings for a cell from its data-instance-id (P0 wiring).
 * Falls back to DEFAULT_SETTINGS for the legacy single default instance.
 * Exported for testing the settings→query path.
 */
export function resolveTokensSettings(el: HTMLElement): TokensSettings {
  const instanceId = el.dataset['instanceId'] ?? null;
  if (instanceId) {
    const inst = getInstance(instanceId);
    if (inst) {
      return { range: _coerceRange(inst.settings?.['range']) };
    }
  }
  return { ...DEFAULT_SETTINGS };
}

// ─── Pure data-shaping ────────────────────────────────────────────────────────

/**
 * Convert the tokensByDay JSON response into uPlot series arrays.
 *
 * Returns [timestamps_sec, hive_series, claude_series] — three parallel
 * arrays of the same length, ready to pass to uPlot as `data`.
 */
export function tokensByDayToUplot(
  entries: TokensByDayEntry[],
): [number[], number[], number[]] {
  const timestamps: number[] = [];
  const hive: number[] = [];
  const claude: number[] = [];
  for (const e of entries) {
    // Parse 'YYYY-MM-DD' as UTC midnight → Unix epoch seconds.
    const epochMs = Date.parse(e.date + 'T00:00:00Z');
    if (!Number.isFinite(epochMs)) continue;
    timestamps.push(epochMs / 1_000);
    hive.push(e.hive);
    claude.push(e.claude);
  }
  return [timestamps, hive, claude];
}

// ─── CSS-var reader ───────────────────────────────────────────────────────────

function cssHex(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

// ─── uPlot options factory — reads CSS vars at call-time ─────────────────────

function makeOpts(w: number, h: number): uPlot.Options {
  const faint = cssHex('--hex-faint', '#8a8780');
  const line  = cssHex('--hex-line',  '#363c30');
  const green = cssHex('--hex-green', '#5cc870');
  const cyan  = cssHex('--hex-cyan',  '#60c8c8');

  return {
    width:  w,
    height: h,
    padding: [8, 8, 0, 0],
    cursor: { show: false },
    legend: { show: false },
    axes: [
      {
        // x-axis: dates
        stroke: faint,
        ticks:  { stroke: line, width: 1 },
        grid:   { stroke: line, width: 1 },
        values: (_u: uPlot, vals: number[]) =>
          vals.map(v => {
            const d = new Date(v * 1_000);
            return `${String(d.getUTCMonth() + 1).padStart(2, '0')}/${String(d.getUTCDate()).padStart(2, '0')}`;
          }),
        font: '9px "JetBrains Mono", ui-monospace, monospace',
      },
      {
        // y-axis: token counts
        stroke: faint,
        ticks:  { stroke: line, width: 1 },
        grid:   { stroke: line, width: 1 },
        values: (_u: uPlot, vals: number[]) =>
          vals.map(v => {
            if (v == null) return '';
            if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M';
            if (v >= 1e3) return (v / 1e3).toFixed(0) + 'k';
            return String(v);
          }),
        font: '9px "JetBrains Mono", ui-monospace, monospace',
        size: 40,
      },
    ],
    // hive = green, claude = cyan (canon: telemetry/system data).
    series: [
      {},  // x (timestamps)
      {
        label:  'Hive',
        stroke: green,
        fill:   `${green}1a`,
        width:  1.5,
      },
      {
        label:  'Claude',
        stroke: cyan,
        fill:   `${cyan}1a`,
        width:  1.5,
      },
    ],
  };
}

// ─── Per-cell view state ──────────────────────────────────────────────────────

interface CellState {
  instanceId: string | null;
  plot: uPlot | null;
  legend: HTMLElement | null;
  select: HTMLSelectElement | null;
  lastData: TokensByDayEntry[];
  /** The range the last fetch was keyed on (so a change triggers a re-fetch). */
  lastRange: TokensRange | '';
  ticks: number;
  suspended: boolean;
  /** Monotonic request id so a stale in-flight fetch can't overwrite a newer one. */
  reqSeq: number;
}

// Per-cell state, keyed by the cell element. WeakMap = no leak when cells drop.
const _cells = new WeakMap<HTMLElement, CellState>();

const _REFRESH_TICKS = 60; // refresh data every ~60 state ticks (~1 min)

// ─── Mount ────────────────────────────────────────────────────────────────────

function mount(el: HTMLElement): void {
  const settings = resolveTokensSettings(el);

  const state: CellState = {
    instanceId: el.dataset['instanceId'] ?? null,
    plot: null,
    legend: null,
    select: null,
    lastData: [],
    lastRange: '',
    ticks: 0,
    suspended: false,
    reqSeq: 0,
  };
  _cells.set(el, state);

  el.style.background = 'var(--panel, #14110f)';
  el.style.borderRadius = '10px';
  el.style.padding = '8px 10px 6px';
  el.style.position = 'relative';
  el.style.display = 'flex';
  el.style.flexDirection = 'column';

  const optionsHtml = RANGE_OPTIONS.map(
    (o) =>
      `<option value="${o.value}"${o.value === settings.range ? ' selected' : ''}>${escHtml(o.label)}</option>`,
  ).join('');

  el.innerHTML = `
    <div class="panel-header" style="margin-bottom:6px;display:flex;align-items:center;justify-content:space-between;gap:8px">
      <span class="panel-label">TOKENS / DAY</span>
      <select class="tokens-day-range" title="Time range"
        style="background:var(--panel,#14110f);color:var(--faint);border:1px solid var(--line,#363c30);border-radius:5px;font-size:10px;font-family:var(--font-mono);padding:1px 4px;cursor:pointer">
        ${optionsHtml}
      </select>
    </div>
    <div class="tokens-day-plot" style="width:100%;flex:1;min-height:0"></div>
    <div class="tokens-day-legend" style="display:flex;gap:12px;margin-top:4px;font-size:10px;font-family:var(--font-mono);color:var(--faint)"></div>
    <div class="tokens-day-loading" style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);font-size:11px;color:var(--faint)">waiting for gateway…</div>
  `;

  state.legend = el.querySelector('.tokens-day-legend');
  state.select = el.querySelector<HTMLSelectElement>('.tokens-day-range');

  // Inline control: change → persist to the SAME instance settings as the gear,
  // then re-fetch with the new range. Mouse-only (native <select>).
  if (state.select) {
    state.select.addEventListener('change', () => {
      const next = _coerceRange(state.select!.value);
      if (state.instanceId) {
        updateInstanceSettings(state.instanceId, { range: next });
      }
      // Re-fetch immediately against the new range.
      void _fetchAndDraw(el, state, next);
    });
  }

  // Kick off first fetch immediately.
  void _fetchAndDraw(el, state, settings.range);
}

// ─── Fetch + draw ─────────────────────────────────────────────────────────────

async function _fetchAndDraw(
  el: HTMLElement,
  state: CellState,
  range: TokensRange,
): Promise<void> {
  const seq = ++state.reqSeq;
  state.lastRange = range;

  const entries = await tokensByDay(rangeToDays(range));
  if (seq !== state.reqSeq) return; // superseded by a newer fetch
  if (!_cells.has(el)) return; // cell dropped

  state.lastData = entries;
  _applyData(el, state, entries);
}

function _applyData(el: HTMLElement, state: CellState, entries: TokensByDayEntry[]): void {
  const loading = el.querySelector<HTMLElement>('.tokens-day-loading');
  if (loading) loading.style.display = 'none';

  if (!entries.length) return;

  const [ts, hive, claude] = tokensByDayToUplot(entries);

  const plotEl = el.querySelector<HTMLElement>('.tokens-day-plot');
  if (!plotEl) return;

  const w = Math.max(200, plotEl.offsetWidth || el.offsetWidth || 300);
  // Fill the available cell height (flex:1 container) instead of a fixed 90px.
  const h = Math.max(90, plotEl.clientHeight || plotEl.offsetHeight || 90);

  if (state.plot) {
    // Reuse existing plot — just update data + possibly resize.
    state.plot.setSize({ width: w, height: h });
    state.plot.setData([ts, hive, claude]);
  } else {
    state.plot = new uPlot(makeOpts(w, h), [ts, hive, claude], plotEl);
  }

  // Update legend with latest-day values.
  if (state.legend) {
    const last = entries[entries.length - 1]!;
    const fmt = (v: number) =>
      v >= 1e6 ? (v / 1e6).toFixed(1) + 'M' : v >= 1e3 ? (v / 1e3).toFixed(1) + 'k' : String(v);
    state.legend.innerHTML =
      `<span style="color:var(--green)">&#9632; hive ${fmt(last.hive)}</span>` +
      `<span style="color:var(--cyan)">&#9632; claude ${fmt(last.claude)}</span>` +
      `<span style="color:var(--faint)">(${last.date})</span>`;
  }
}

// ─── Update ───────────────────────────────────────────────────────────────────

function update(state: SystemState, budget: RenderBudget): void {
  if (!state.gatewayUp) return;
  if (budget.chartFps <= 0) return;

  // Drive every mounted tokens-day cell. We discover cells from the DOM so each
  // instance (with its own data-instance-id) refreshes against its own range.
  const cells = document.querySelectorAll<HTMLElement>('[data-plugin-id="tokens-day"]');
  cells.forEach((el) => {
    const cs = _cells.get(el);
    if (!cs || cs.suspended) return;

    const settings = resolveTokensSettings(el);

    // Range changed externally (e.g. via the gear form) → re-fetch immediately.
    if (settings.range !== cs.lastRange) {
      cs.ticks = 0;
      // Keep the inline <select> in sync with the gear-driven change.
      if (cs.select && cs.select.value !== settings.range) {
        cs.select.value = settings.range;
      }
      void _fetchAndDraw(el, cs, settings.range);
      return;
    }

    // Periodic refresh.
    cs.ticks++;
    if (cs.ticks >= _REFRESH_TICKS) {
      cs.ticks = 0;
      void _fetchAndDraw(el, cs, settings.range);
    }
  });
}

// ─── Resize ───────────────────────────────────────────────────────────────────

function onResize(rect: Rect): void {
  const cells = document.querySelectorAll<HTMLElement>('[data-plugin-id="tokens-day"]');
  cells.forEach((el) => {
    const cs = _cells.get(el);
    if (!cs || !cs.plot || !cs.lastData.length) return;
    const w = Math.max(200, rect.w - 20);
    const plotEl = el.querySelector<HTMLElement>('.tokens-day-plot');
    const h = Math.max(90, plotEl?.clientHeight || (rect.h - 70));
    cs.plot.setSize({ width: w, height: h });
  });
}

// ─── Suspend / resume ─────────────────────────────────────────────────────────

function suspend(): void {
  const cells = document.querySelectorAll<HTMLElement>('[data-plugin-id="tokens-day"]');
  cells.forEach((el) => {
    const cs = _cells.get(el);
    if (cs) cs.suspended = true;
  });
}

function resume(): void {
  const cells = document.querySelectorAll<HTMLElement>('[data-plugin-id="tokens-day"]');
  cells.forEach((el) => {
    const cs = _cells.get(el);
    if (cs) {
      cs.suspended = false;
      cs.ticks = _REFRESH_TICKS; // trigger refresh on next tick
    }
  });
}

// ─── Relevance ────────────────────────────────────────────────────────────────

function relevance(state: SystemState): RelevanceResult {
  // Hide in gaming tier — don't compete for screen space.
  if (state.tier === 'gaming') return { priority: 0,  size: 'hidden' };
  // Always show when gateway is up.
  if (!state.gatewayUp)        return { priority: 10, size: 'min' };
  return { priority: 45, size: 'sm' };
}

// ─── Theme change: re-create every cell's plot with new theme colors ─────────

function _onThemeChange(): void {
  const cells = document.querySelectorAll<HTMLElement>('[data-plugin-id="tokens-day"]');
  cells.forEach((el) => {
    const cs = _cells.get(el);
    if (!cs || !cs.plot || !cs.lastData.length) return;
    const plotEl = el.querySelector<HTMLElement>('.tokens-day-plot');
    if (!plotEl) return;
    const w = Math.max(200, plotEl.offsetWidth || el.offsetWidth || 300);
    const h = Math.max(90, plotEl.clientHeight || plotEl.offsetHeight || 90);
    cs.plot.destroy();
    cs.plot = new uPlot(makeOpts(w, h), tokensByDayToUplot(cs.lastData), plotEl);
    // Legend spans use CSS vars natively; re-paint with latest-day values.
    if (cs.legend && cs.lastData.length) {
      const last = cs.lastData[cs.lastData.length - 1]!;
      const fmt = (v: number) =>
        v >= 1e6 ? (v / 1e6).toFixed(1) + 'M' : v >= 1e3 ? (v / 1e3).toFixed(1) + 'k' : String(v);
      cs.legend.innerHTML =
        `<span style="color:var(--green)">&#9632; hive ${fmt(last.hive)}</span>` +
        `<span style="color:var(--cyan)">&#9632; claude ${fmt(last.claude)}</span>` +
        `<span style="color:var(--faint)">(${last.date})</span>`;
    }
  });
}

// Guard module-load side effect so the module is importable under vitest's
// node env (no `window`). `window` always exists in the browser/Lively, so
// this never changes runtime behavior.
if (typeof window !== 'undefined') {
  window.addEventListener('hive-theme-change', _onThemeChange);
}

// ─── Plugin definition ────────────────────────────────────────────────────────

const tokensDayPlugin: PanelPlugin = {
  id:          'tokens-day',
  title:       'TOKENS / DAY',
  dataSources: [
    { kind: 'poll', endpoint: '/board/tokens-by-day', intervalKey: 'board' },
  ],
  relevance,
  mount,
  update,
  onResize,
  suspend,
  resume,
  defaultSettings: { ...DEFAULT_SETTINGS },
  settingsSchema: {
    fields: [
      {
        key: 'range',
        label: 'Time range',
        type: 'select',
        default: '30d',
        options: RANGE_OPTIONS.map((o) => ({ value: o.value, label: o.label })),
        hint: 'Days of token history to chart',
      },
    ],
  },
};

register(tokensDayPlugin);
export { tokensDayPlugin };
