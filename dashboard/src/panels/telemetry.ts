/**
 * panels/telemetry.ts — uPlot time-series telemetry wall.
 *
 * Charts rendered:
 *   - Token rate (hive vs claude lines)
 *   - Cost $
 *   - Throughput done/h
 *   - Smoke pass %
 *   - Parse-fail rate (red alarm > 5%)
 *   - Pipeline by_status mini bars
 *
 * Data source: rolling in-memory buffer of BoardStatsSample (pushed by main.ts
 * each poll cycle). History is built client-side since the gateway's
 * /board/stats endpoint does not expose a time-series.
 */

import {
  createTokenRateChart,
  createCostChart,
  createThroughputChart,
  createSmokeChart,
  type Chart,
} from '../charts/uplot_factory.js';
import type { BoardStatsSample } from '../types.js';
import { bufferToUplot, throughputPerHour, fmtPct, fmtNum, escHtml } from '../format.js';
import type { BoardStats } from '../gateway.js';

// ─── Chart instances ──────────────────────────────────────────────────────────

interface ChartSet {
  tokenRate: Chart;
  cost:      Chart;
  throughput: Chart;
  smoke:     Chart;
}

let charts: ChartSet | null = null;

// ─── Panel widths (approximate fractions of center column) ───────────────────
const SMALL_W  = 340;
const SMALL_H  = 100;
const WIDE_W   = 720;
const WIDE_H   = 110;

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
  const tokenContainer = _makeChartCell('TOKEN RATE', 'token-rate-chart');
  tokenRow.appendChild(tokenContainer);
  panel.appendChild(tokenRow);

  // Row 2: cost + throughput + smoke (three small). Parse-fail + the pipeline
  // mini-bars + the (stub) activity ticker were dropped — they crammed the md
  // cell so nothing was legible. Token rate + these three is the readable set.
  const row2 = _makeRow('charts-row-2');
  row2.appendChild(_makeChartCell('COST $',      'cost-chart'));
  row2.appendChild(_makeChartCell('DONE/H',      'throughput-chart'));
  row2.appendChild(_makeChartCell('SMOKE PASS',  'smoke-chart'));
  panel.appendChild(row2);

  // Instantiate uPlot charts
  charts = {
    tokenRate:  createTokenRateChart(_el('token-rate-chart')!,   WIDE_W,  WIDE_H),
    cost:       createCostChart(     _el('cost-chart')!,         SMALL_W, SMALL_H),
    throughput: createThroughputChart(_el('throughput-chart')!,  SMALL_W, SMALL_H),
    smoke:      createSmokeChart(    _el('smoke-chart')!,        SMALL_W, SMALL_H),
  };
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

// ─── Update pipeline mini bars ────────────────────────────────────────────────

export function updatePipelineBars(stats: BoardStats): void {
  const el = document.getElementById('pipeline-bars');
  if (!el) return;

  const byStatus = stats.by_status ?? {};
  const statuses = ['backlog', 'ready', 'in_progress', 'qa', 'review', 'blocked', 'done'];
  const colours: Record<string, string> = {
    backlog:     '#363c30',
    ready:       '#e0a030',
    in_progress: '#c07840',
    qa:          '#60c8c8',
    review:      '#ffb94d',
    blocked:     '#c44040',
    done:        '#5cc870',
  };

  const max = Math.max(1, ...Object.values(byStatus));

  el.innerHTML = statuses.map((s) => {
    const count = byStatus[s] ?? 0;
    const pct   = Math.round((count / max) * 100);
    const colour = colours[s] ?? '#8a8780';
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
