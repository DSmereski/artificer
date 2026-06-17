/**
 * uplot_factory.ts — Thin wrappers around uPlot for the telemetry charts.
 *
 * Each factory returns an { update(data) } object so callers do not
 * need to hold the raw uPlot instance.  All charts are canvas-based.
 *
 * Colour palette mirrors DESIGN.md tokens (hex values):
 *   hive tokens  → green  #5cc870
 *   claude tokens→ cyan   #60c8c8  (telemetry/system data per the canon)
 *   cost         → cyan   #60c8c8
 *   smoke pass   → green  #5cc870
 *   parse fail   → red    #c44040
 *   throughput   → amber  #e0a030
 */

import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';

// ─── Shared theme ─────────────────────────────────────────────────────────────

const THEME = {
  bg:      '#121610',
  grid:    '#363c30',
  label:   '#8a8780',
  ink:     '#f2f0ec',
  green:   '#5cc870',
  /* claude = cyan per the canon; `violet` retained as an alias so the
     series wiring below stays untouched. */
  violet:  '#60c8c8',
  amber:   '#e0a030',
  cyan:    '#60c8c8',
  red:     '#c44040',
  copper:  '#c07840',
};

function baseOpts(
  width: number,
  height: number,
  series: uPlot.Series[],
  axes: uPlot.Axis[],
  scales?: Record<string, uPlot.Scale>,
): uPlot.Options {
  return {
    width,
    height,
    pxAlign: true,
    cursor: { show: false },
    legend: { show: false },
    series,
    axes,
    scales: { x: { time: true }, ...scales },
  };
}

function xAxis(): uPlot.Axis {
  return {
    stroke:    THEME.label,
    ticks:     { stroke: THEME.grid, width: 1 },
    grid:      { stroke: THEME.grid, width: 1, dash: [2, 4] },
    font:      '10px "JetBrains Mono", monospace',
    labelFont: '10px "JetBrains Mono", monospace',
  };
}

function yAxis(label?: string): uPlot.Axis {
  return {
    stroke:    THEME.label,
    ticks:     { stroke: THEME.grid, width: 1 },
    grid:      { stroke: THEME.grid, width: 1, dash: [2, 4] },
    font:      '10px "JetBrains Mono", monospace',
    labelFont: '10px "JetBrains Mono", monospace',
    label,
  };
}

export interface Chart {
  update(data: uPlot.AlignedData): void;
  destroy(): void;
}

// ─── Token rate (hive + claude stacked/lines) ─────────────────────────────────

export function createTokenRateChart(container: HTMLElement, w: number, h: number): Chart {
  const opts = baseOpts(w, h, [
    { label: 'time' },
    {
      label:  'Hive',
      stroke: THEME.green,
      width:  2,
      fill:   `${THEME.green}20`,
      spanGaps: true,
    },
    {
      label:  'Claude',
      stroke: THEME.violet,
      width:  2,
      fill:   `${THEME.violet}20`,
      spanGaps: true,
    },
  ], [xAxis(), yAxis('tokens')]);

  const plot = new uPlot(opts, [[], [], []], container);
  return {
    update(data) { plot.setData(data); },
    destroy()    { plot.destroy(); },
  };
}

// ─── Cost ($) ─────────────────────────────────────────────────────────────────

export function createCostChart(container: HTMLElement, w: number, h: number): Chart {
  const opts = baseOpts(w, h, [
    { label: 'time' },
    {
      label:  'Cost $',
      stroke: THEME.cyan,
      width:  2,
      fill:   `${THEME.cyan}18`,
      spanGaps: true,
      value:  (_u, v) => (v != null ? `$${v.toFixed(3)}` : '--'),
    },
  ], [xAxis(), yAxis('$')]);

  const plot = new uPlot(opts, [[], []], container);
  return {
    update(data) { plot.setData(data); },
    destroy()    { plot.destroy(); },
  };
}

// ─── Throughput (done/h) ──────────────────────────────────────────────────────

export function createThroughputChart(container: HTMLElement, w: number, h: number): Chart {
  const opts = baseOpts(w, h, [
    { label: 'time' },
    {
      label:  'Done/h',
      stroke: THEME.amber,
      width:  2,
      fill:   `${THEME.amber}20`,
      spanGaps: true,
    },
  ], [xAxis(), yAxis('done/h')]);

  const plot = new uPlot(opts, [[], []], container);
  return {
    update(data) { plot.setData(data); },
    destroy()    { plot.destroy(); },
  };
}

// ─── Smoke pass % ─────────────────────────────────────────────────────────────

export function createSmokeChart(container: HTMLElement, w: number, h: number): Chart {
  const opts = baseOpts(w, h, [
    { label: 'time' },
    {
      label:  'Smoke%',
      stroke: THEME.green,
      width:  2,
      fill:   `${THEME.green}18`,
      spanGaps: true,
      value:  (_u, v) => (v != null ? `${Math.round(v)}%` : '--'),
    },
  ], [
    xAxis(),
    yAxis('%'),
  ], {
    y: { range: (_u, _min, _max) => [0, 100] },
  });

  const plot = new uPlot(opts, [[], []], container);
  return {
    update(data) { plot.setData(data); },
    destroy()    { plot.destroy(); },
  };
}

// ─── Parse-fail rate (with alarm colour when > 5%) ───────────────────────────

export function createParseFailChart(container: HTMLElement, w: number, h: number): Chart {
  const opts = baseOpts(w, h, [
    { label: 'time' },
    {
      label:  'ParseFail%',
      stroke: THEME.red,
      width:  2,
      fill:   `${THEME.red}20`,
      spanGaps: true,
      value:  (_u, v) => (v != null ? `${(v * 100).toFixed(1)}%` : '--'),
    },
  ], [
    xAxis(),
    yAxis('fail%'),
  ], {
    y: { range: (_u, _min, _max) => [0, 0.2] },
  });

  const plot = new uPlot(opts, [[], []], container);
  return {
    update(data) { plot.setData(data); },
    destroy()    { plot.destroy(); },
  };
}

// ─── GPU util sparkline (single card) ────────────────────────────────────────

export function createGpuSparkline(container: HTMLElement, w: number, h: number): Chart {
  const opts = baseOpts(w, h, [
    { label: 'time' },
    {
      label:  'Util%',
      stroke: THEME.copper,
      width:  1.5,
      spanGaps: true,
    },
  ], [
    { ...xAxis(), show: false },
    { ...yAxis(), show: false },
  ], {
    y: { range: (_u, _min, _max) => [0, 100] },
  });

  const plot = new uPlot(opts, [[], []], container);
  return {
    update(data) { plot.setData(data); },
    destroy()    { plot.destroy(); },
  };
}
