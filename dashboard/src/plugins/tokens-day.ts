/**
 * plugins/tokens-day.ts — Tokens-per-day uPlot line chart panel.
 *
 * Fetches /board/tokens-by-day (open, no auth) and renders a two-series
 * uPlot line chart: hive tokens (green) and claude tokens (cyan).
 * Copper/amber dark theme to match the dashboard aesthetic.
 *
 * Activates once the gateway endpoint is available (gateway restart after
 * the crew-board feat); shows an empty/loading state until then.
 */

import uPlot from 'uplot';
import { register } from './registry.js';
import type { PanelPlugin, RelevanceResult, Rect } from './contract.js';
import type { SystemState, RenderBudget } from '../state/types.js';
import { tokensByDay } from '../gateway.js';
import type { TokensByDayEntry } from '../gateway.js';

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

// ─── uPlot options factory ────────────────────────────────────────────────────

function makeOpts(w: number, h: number): uPlot.Options {
  return {
    width:  w,
    height: h,
    padding: [8, 8, 0, 0],
    cursor: { show: false },
    legend: { show: false },
    axes: [
      {
        // x-axis: dates
        stroke:     '#8a8780',
        ticks:      { stroke: '#363c30', width: 1 },
        grid:       { stroke: '#363c30', width: 1 },
        values:     (_u: uPlot, vals: number[]) =>
          vals.map(v => {
            const d = new Date(v * 1_000);
            return `${String(d.getUTCMonth() + 1).padStart(2, '0')}/${String(d.getUTCDate()).padStart(2, '0')}`;
          }),
        font:       '9px "JetBrains Mono", ui-monospace, monospace',
      },
      {
        // y-axis: token counts
        stroke:     '#8a8780',
        ticks:      { stroke: '#363c30', width: 1 },
        grid:       { stroke: '#363c30', width: 1 },
        values:     (_u: uPlot, vals: number[]) =>
          vals.map(v => {
            if (v == null) return '';
            if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M';
            if (v >= 1e3) return (v / 1e3).toFixed(0) + 'k';
            return String(v);
          }),
        font:       '9px "JetBrains Mono", ui-monospace, monospace',
        size:       40,
      },
    ],
    // hive = green, claude = cyan (canon: telemetry/system data).
    series: [
      {},  // x (timestamps)
      {
        label:  'Hive',
        stroke: '#5cc870',
        fill:   'rgba(92,200,112,0.10)',
        width:  1.5,
      },
      {
        label:  'Claude',
        stroke: '#60c8c8',
        fill:   'rgba(96,200,200,0.10)',
        width:  1.5,
      },
    ],
  };
}

// ─── Module state ─────────────────────────────────────────────────────────────

let _plot:       uPlot | null = null;
let _container:  HTMLElement | null = null;
let _legend:     HTMLElement | null = null;
let _suspended   = false;
let _lastData:   TokensByDayEntry[] = [];

// ─── Relevance ────────────────────────────────────────────────────────────────

function relevance(state: SystemState): RelevanceResult {
  // Hide in gaming tier — don't compete for screen space.
  if (state.tier === 'gaming') return { priority: 0,  size: 'hidden' };
  // Always show when gateway is up.
  if (!state.gatewayUp)        return { priority: 10, size: 'min' };
  return { priority: 45, size: 'sm' };
}

// ─── Mount ────────────────────────────────────────────────────────────────────

function mount(el: HTMLElement): void {
  _container = el;
  el.style.background = 'var(--panel, #14110f)';
  el.style.borderRadius = '10px';
  el.style.padding = '8px 10px 6px';
  el.style.position = 'relative';
  el.style.display = 'flex';
  el.style.flexDirection = 'column';

  el.innerHTML = `
    <div class="panel-header" style="margin-bottom:6px">
      <span class="panel-label">TOKENS / DAY</span>
    </div>
    <div id="tokens-day-plot" style="width:100%;flex:1;min-height:0"></div>
    <div id="tokens-day-legend" style="display:flex;gap:12px;margin-top:4px;font-size:10px;font-family:var(--font-mono);color:var(--faint)"></div>
    <div id="tokens-day-loading" style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);font-size:11px;color:var(--faint)">waiting for gateway…</div>
  `;

  _legend = el.querySelector('#tokens-day-legend');

  // Kick off first fetch immediately.
  void _fetchAndDraw();
}

// ─── Fetch + draw ─────────────────────────────────────────────────────────────

async function _fetchAndDraw(): Promise<void> {
  const entries = await tokensByDay(30);
  if (!_container) return;
  _lastData = entries;
  _applyData(entries);
}

function _applyData(entries: TokensByDayEntry[]): void {
  if (!_container) return;

  const loading = _container.querySelector<HTMLElement>('#tokens-day-loading');
  if (loading) loading.style.display = 'none';

  if (!entries.length) return;

  const [ts, hive, claude] = tokensByDayToUplot(entries);

  const plotEl = _container.querySelector<HTMLElement>('#tokens-day-plot');
  if (!plotEl) return;

  const w = Math.max(200, plotEl.offsetWidth || _container.offsetWidth || 300);
  // Fill the available cell height (flex:1 container) instead of a fixed 90px.
  const h = Math.max(90, plotEl.clientHeight || plotEl.offsetHeight || 90);

  if (_plot) {
    // Reuse existing plot — just update data + possibly resize.
    _plot.setSize({ width: w, height: h });
    _plot.setData([ts, hive, claude]);
  } else {
    _plot = new uPlot(makeOpts(w, h), [ts, hive, claude], plotEl);
  }

  // Update legend with latest-day values.
  if (_legend) {
    const last = entries[entries.length - 1];
    const fmt = (v: number) =>
      v >= 1e6 ? (v / 1e6).toFixed(1) + 'M' : v >= 1e3 ? (v / 1e3).toFixed(1) + 'k' : String(v);
    _legend.innerHTML =
      `<span style="color:#5cc870">&#9632; hive ${fmt(last.hive)}</span>` +
      `<span style="color:#60c8c8">&#9632; claude ${fmt(last.claude)}</span>` +
      `<span style="color:var(--faint)">(${last.date})</span>`;
  }
}

// ─── Update ───────────────────────────────────────────────────────────────────

let _ticksSinceRefresh = 0;
const _REFRESH_TICKS = 60;   // refresh data every ~60 state ticks (~1 min)

function update(state: SystemState, budget: RenderBudget): void {
  if (_suspended) return;
  if (!state.gatewayUp) return;
  if (budget.chartFps <= 0) return;

  _ticksSinceRefresh++;
  if (_ticksSinceRefresh >= _REFRESH_TICKS) {
    _ticksSinceRefresh = 0;
    void _fetchAndDraw();
  }
}

// ─── Resize ───────────────────────────────────────────────────────────────────

function onResize(rect: Rect): void {
  if (!_plot || !_lastData.length) return;
  const w = Math.max(200, rect.w - 20);
  const plotEl = _container?.querySelector<HTMLElement>('#tokens-day-plot');
  const h = Math.max(90, plotEl?.clientHeight || (rect.h - 70));
  _plot.setSize({ width: w, height: h });
}

// ─── Suspend / resume ─────────────────────────────────────────────────────────

function suspend(): void  { _suspended = true;  }
function resume():  void  {
  _suspended = false;
  _ticksSinceRefresh = _REFRESH_TICKS;  // trigger refresh on next tick
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
};

register(tokensDayPlugin);
export { tokensDayPlugin };
