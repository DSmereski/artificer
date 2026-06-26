/**
 * panels/telemetry.ts — uPlot time-series telemetry wall.
 *
 * Charts rendered:
 *   Row 1: TOKEN RATE — hive vs claude rolling samples (wide)
 *   Row 2: COST USD · DONE/H · SMOKE PASS % (three small)
 *   Row 3: TOKENS / DAY — hive vs claude, last 30 days from /board/tokens-by-day (wide)
 *
 * Styling for all charts follows the tokens-day reference aesthetic:
 *   • padding [8,8,0,0], no cursor/legend, no grid dash
 *   • axis stroke --hex-faint, tick/grid stroke --hex-line
 *   • font 9px JetBrains Mono, y-axis size 40
 *   • series fill ${color}1a, stroke width 1.5
 *   • y-axis value formatters: K/M, $, % as appropriate
 *
 * Rolling buffer data source: BoardStatsSample pushed by plugins/telemetry.ts
 * each poll cycle. Tokens/day fetched independently from /board/tokens-by-day.
 */

import {
  createTokenRateChart,
  createCostChart,
  createThroughputChart,
  createSmokeChart,
  createTokensByDayChart,
  type Chart,
} from '../charts/uplot_factory.js';
import type { BoardStatsSample } from '../types.js';
import { bufferToUplot, throughputPerHour, fmtPct, fmtNum, escHtml } from '../format.js';
import type { BoardStats } from '../gateway.js';
import { tokensByDay, type TokensByDayEntry } from '../gateway.js';

// ─── Chart instances ──────────────────────────────────────────────────────────

interface ChartSet {
  tokenRate:    Chart;
  cost:         Chart;
  throughput:   Chart;
  smoke:        Chart;
  tokensByDay:  Chart;
}

let charts: ChartSet | null = null;

// ─── Panel widths (approximate fractions of center column) ───────────────────
const SMALL_W  = 340;
const SMALL_H  = 100;
const WIDE_W   = 720;
const WIDE_H   = 110;

// ─── Tokens/day state ─────────────────────────────────────────────────────────
/** Raw gateway data, kept so the legend can be refreshed on theme change. */
let _tokensByDayEntries: TokensByDayEntry[] = [];

// ─── Init ─────────────────────────────────────────────────────────────────────

export function initTelemetryPanel(): void {
  const panel = document.getElementById('telemetry-charts');
  if (!panel) {
    console.warn('[telemetry] #telemetry-charts not found');
    return;
  }
  panel.innerHTML = '';

  // Row 1: token rate (wide)
  const tokenRow = _makeRow('token-rate-row');
  tokenRow.appendChild(_makeChartCell('TOKEN RATE — HIVE VS CLAUDE', 'token-rate-chart'));
  panel.appendChild(tokenRow);

  // Row 2: cost + throughput + smoke (three small).
  const row2 = _makeRow('charts-row-2');
  row2.appendChild(_makeChartCell('COST USD',     'cost-chart'));
  row2.appendChild(_makeChartCell('DONE / H',     'throughput-chart'));
  row2.appendChild(_makeChartCell('SMOKE PASS %', 'smoke-chart'));
  panel.appendChild(row2);

  // Row 3: tokens/day — hive vs claude, last 30 days (wide).
  // Folded in from the standalone tokens-day panel.
  const row3 = _makeRow('tokens-day-row');
  row3.appendChild(_makeChartCell('TOKENS / DAY — HIVE VS CLAUDE (30 d)', 'tokens-day-chart'));
  const legendEl = document.createElement('div');
  legendEl.id = 'tokens-day-legend';
  legendEl.style.cssText = 'display:flex;gap:12px;margin-top:4px;padding:0 8px 4px;font-size:10px;font-family:var(--font-mono);color:var(--faint)';
  row3.appendChild(legendEl);
  panel.appendChild(row3);

  // Instantiate uPlot charts
  charts = {
    tokenRate:   createTokenRateChart(   _el('token-rate-chart')!,  WIDE_W,  WIDE_H),
    cost:        createCostChart(        _el('cost-chart')!,        SMALL_W, SMALL_H),
    throughput:  createThroughputChart(  _el('throughput-chart')!,  SMALL_W, SMALL_H),
    smoke:       createSmokeChart(       _el('smoke-chart')!,       SMALL_W, SMALL_H),
    tokensByDay: createTokensByDayChart( _el('tokens-day-chart')!,  WIDE_W,  WIDE_H),
  };

  // Fetch tokens/day data immediately.
  void _fetchAndDrawTokensByDay();
}

// ─── Update from rolling buffer ───────────────────────────────────────────────

export function updateTelemetryCharts(buf: BoardStatsSample[]): void {
  if (!charts) return;
  if (buf.length === 0) return;

  const [ts, hive]     = bufferToUplot(buf, (s) => s.hive_tokens);
  const [,    claude]   = bufferToUplot(buf, (s) => s.claude_tokens);
  const [,    cost]     = bufferToUplot(buf, (s) => s.cost_usd);
  const [,    smoke]    = bufferToUplot(buf, (s) => s.smoke_pass_pct);

  // Compute per-sample throughput from consecutive diffs
  const throughputVals = buf.map((_, i) => {
    if (i === 0) return 0;
    const prev = buf[i - 1];
    const curr = buf[i];
    const dtH = (curr.ts - prev.ts) / 3_600_000;
    if (dtH <= 0) return 0;
    return Math.max(0, curr.done_count - prev.done_count) / dtH;
  });

  charts.tokenRate.update([ts, hive, claude]);
  charts.cost.update([ts, cost]);
  charts.throughput.update([ts, throughputVals]);
  charts.smoke.update([ts, smoke]);
}

// ─── Tokens/day fetch + render ────────────────────────────────────────────────

/** Fetch from /board/tokens-by-day and push to the chart + legend. */
async function _fetchAndDrawTokensByDay(): Promise<void> {
  const entries = await tokensByDay(30);
  if (!entries.length) return;
  _tokensByDayEntries = entries;
  _applyTokensByDay(entries);
}

function _applyTokensByDay(entries: TokensByDayEntry[]): void {
  if (!charts || !entries.length) return;

  const timestamps: number[] = [];
  const hive: number[] = [];
  const claude: number[] = [];
  for (const e of entries) {
    const epochMs = Date.parse(e.date + 'T00:00:00Z');
    if (!Number.isFinite(epochMs)) continue;
    timestamps.push(epochMs / 1_000);
    hive.push(e.hive);
    claude.push(e.claude);
  }

  charts.tokensByDay.update([timestamps, hive, claude]);

  // Update the inline legend with latest-day values.
  const legendEl = document.getElementById('tokens-day-legend');
  if (legendEl) {
    const last = entries[entries.length - 1];
    const fmt = (v: number) =>
      v >= 1e6 ? (v / 1e6).toFixed(1) + 'M' : v >= 1e3 ? (v / 1e3).toFixed(1) + 'k' : String(v);
    legendEl.innerHTML =
      `<span style="color:var(--green)">&#9632; hive ${fmt(last.hive)}</span>` +
      `<span style="color:var(--cyan)">&#9632; claude ${fmt(last.claude)}</span>` +
      `<span style="color:var(--faint)">(${last.date})</span>`;
  }
}

/** Called by plugins/telemetry.ts on a ~1-minute cadence. */
export function refreshTokensByDay(): void {
  void _fetchAndDrawTokensByDay();
}

/** Called on theme change to re-render the legend (chart re-renders via makeChart handler). */
export function recolorTokensByDayLegend(): void {
  if (_tokensByDayEntries.length) {
    _applyTokensByDay(_tokensByDayEntries);
  }
}

// ─── Update pipeline mini bars ────────────────────────────────────────────────

export function updatePipelineBars(stats: BoardStats): void {
  const el = document.getElementById('pipeline-bars');
  if (!el) return;

  const byStatus = stats.by_status ?? {};
  const statuses = ['backlog', 'ready', 'in_progress', 'qa', 'review', 'blocked', 'done'];
  // Resolve from CSS theme vars so bars re-color when the theme switches.
  const cs = getComputedStyle(document.documentElement);
  const colours: Record<string, string> = {
    backlog:     cs.getPropertyValue('--hex-line').trim()   || '#363c30',
    ready:       cs.getPropertyValue('--hex-amber').trim()  || '#e0a030',
    in_progress: cs.getPropertyValue('--hex-copper').trim() || '#c07840',
    qa:          cs.getPropertyValue('--hex-cyan').trim()   || '#60c8c8',
    review:      cs.getPropertyValue('--hex-amber').trim()  || '#e0a030',
    blocked:     cs.getPropertyValue('--hex-red').trim()    || '#c44040',
    done:        cs.getPropertyValue('--hex-green').trim()  || '#5cc870',
  };

  const max = Math.max(1, ...Object.values(byStatus));

  el.innerHTML = statuses.map((s) => {
    const count = byStatus[s] ?? 0;
    const pct   = Math.round((count / max) * 100);
    const colour = colours[s] ?? (cs.getPropertyValue('--hex-faint').trim() || '#8a8780');
    const label  = s === 'in_progress' ? 'WIP' : s.charAt(0).toUpperCase() + s.slice(1, 3);
    return `
      <div class="pipeline-col">
        <div class="pipeline-bar-track">
          <div class="pipeline-bar-fill" style="height:${pct}%;background:${colour}"></div>
        </div>
        <div class="pipeline-count" style="color:${colour}">${count}</div>
        <div class="pipeline-label">${escHtml(label)}</div>
      </div>
    `;
  }).join('');

  // Also update parse-fail alarm indicator
  const parseFail = stats.parse_fail;
  if (parseFail != null) {
    const alarmEl = document.getElementById('parsefail-alarm');
    if (alarmEl) {
      const isAlarm = parseFail.rate > 0.05;
      alarmEl.textContent = isAlarm
        ? `⚠ ${fmtPct(parseFail.rate * 100)} fail rate!`
        : fmtPct(parseFail.rate * 100);
      alarmEl.className = `parsefail-indicator${isAlarm ? ' alarm' : ''}`;
    }
  }

  // Update throughput readout
  const tpEl = document.getElementById('throughput-val');
  if (tpEl) {
    // Single-point throughput isn't meaningful from a single stats call,
    // but show the cumulative done count here as a fallback.
    const done = byStatus['done'] ?? 0;
    tpEl.textContent = `${fmtNum(done)} done total`;
  }
}

// ─── Activity ticker (Phase C stub) ──────────────────────────────────────────

export function initActivityTicker(): void {
  const el = document.getElementById('activity-ticker');
  if (!el) return;
  el.innerHTML = `
    <div class="ticker-row ticker-stub">
      <span class="ticker-dot"></span>
      <span class="ticker-msg">Activity feed connects in Phase C (WS /v1/events + /board/events)</span>
    </div>
  `;
}

export function prependTickerEvent(msg: string, kind: 'info' | 'alert' | 'done' = 'info'): void {
  const el = document.getElementById('activity-ticker');
  if (!el) return;

  // Remove stub row if present
  el.querySelector('.ticker-stub')?.remove();

  const row = document.createElement('div');
  row.className = `ticker-row ticker-${kind}`;
  row.innerHTML = `
    <span class="ticker-time">${new Date().toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })}</span>
    <span class="ticker-dot"></span>
    <span class="ticker-msg">${escHtml(msg)}</span>
  `;

  el.insertBefore(row, el.firstChild);

  // Cap at 30 rows
  while (el.children.length > 30) {
    el.removeChild(el.lastChild!);
  }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function _makeRow(id: string): HTMLElement {
  const div = document.createElement('div');
  div.className = 'chart-row';
  div.id = id;
  return div;
}

function _makeChartCell(label: string, chartId: string): HTMLElement {
  const cell = document.createElement('div');
  cell.className = 'chart-cell';
  cell.innerHTML = `
    <div class="chart-cell-label">${escHtml(label)}</div>
    <div id="${chartId}" class="chart-canvas-wrap"></div>
  `;
  return cell;
}

function _el(id: string): HTMLElement | null {
  return document.getElementById(id);
}

// Reexport for use in main.ts
export { throughputPerHour };
