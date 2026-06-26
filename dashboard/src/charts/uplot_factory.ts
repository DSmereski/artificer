/**
 * uplot_factory.ts — Thin wrappers around uPlot for the telemetry charts.
 *
 * Each factory returns an { update(data) } object so callers do not
 * need to hold the raw uPlot instance.  All charts are canvas-based.
 *
 * Colour palette mirrors CSS theme tokens (read at render time via
 * getComputedStyle so charts re-color when the theme switches):
 *   hive tokens  → --hex-green
 *   claude tokens→ --hex-cyan   (telemetry/system data per the canon)
 *   cost         → --hex-cyan
 *   smoke pass   → --hex-green
 *   parse fail   → --hex-red
 *   throughput   → --hex-amber
 *
 * Styling follows the tokens-day chart aesthetic (the reference design):
 *   • padding [8, 8, 0, 0], no cursor, no legend
 *   • axis stroke = --hex-faint, tick/grid stroke = --hex-line (no dash)
 *   • font 9px JetBrains Mono, y-axis size: 40
 *   • series fill = ${color}1a (10% alpha), stroke width 1.5
 *   • y-axis value formatter: K/M suffix for token counts, $ prefix for cost,
 *     % suffix for rates, bare number otherwise
 */

import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';

// ─── CSS-var reader ───────────────────────────────────────────────────────────

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// ─── Live theme snapshot (reads CSS vars every call) ─────────────────────────

function getTheme() {
  return {
    bg:     cssVar('--hex-bg')     || '#121610',
    grid:   cssVar('--hex-line')   || '#363c30',
    label:  cssVar('--hex-faint')  || '#8a8780',
    ink:    cssVar('--hex-ink')    || '#f2f0ec',
    green:  cssVar('--hex-green')  || '#5cc870',
    violet: cssVar('--hex-cyan')   || '#60c8c8',
    amber:  cssVar('--hex-amber')  || '#e0a030',
    cyan:   cssVar('--hex-cyan')   || '#60c8c8',
    red:    cssVar('--hex-red')    || '#c44040',
    copper: cssVar('--hex-copper') || '#c07840',
  };
}

/** Shared padding — matches the tokens-day chart reference. */
const CHART_PADDING: [number, number, number, number] = [8, 8, 0, 0];

/** Date formatter for x-axis: MM/DD (UTC), matches tokens-day. */
function fmtDate(_u: uPlot, vals: number[]): string[] {
  return vals.map(v => {
    const d = new Date(v * 1_000);
    return `${String(d.getUTCMonth() + 1).padStart(2, '0')}/${String(d.getUTCDate()).padStart(2, '0')}`;
  });
}

/** Token/large-number formatter: M/k suffix. */
function fmtTokenVal(_u: uPlot, vals: number[]): string[] {
  return vals.map(v => {
    if (v == null) return '';
    if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M';
    if (v >= 1e3) return (v / 1e3).toFixed(0) + 'k';
    return String(v);
  });
}

/** USD cost formatter. */
function fmtCostVal(_u: uPlot, vals: number[]): string[] {
  return vals.map(v => (v == null ? '' : `$${v.toFixed(2)}`));
}

/** Percentage formatter (0-100 range). */
function fmtPctVal(_u: uPlot, vals: number[]): string[] {
  return vals.map(v => (v == null ? '' : `${Math.round(v)}%`));
}

/** Done/h formatter. */
function fmtRateVal(_u: uPlot, vals: number[]): string[] {
  return vals.map(v => {
    if (v == null) return '';
    if (v >= 1e3) return (v / 1e3).toFixed(1) + 'k';
    return String(Math.round(v));
  });
}

function xAxis(): uPlot.Axis {
  const T = getTheme();
  return {
    stroke:    T.label,
    ticks:     { stroke: T.grid, width: 1 },
    grid:      { stroke: T.grid, width: 1 },
    font:      '9px "JetBrains Mono", ui-monospace, monospace',
    labelFont: '9px "JetBrains Mono", ui-monospace, monospace',
    values:    fmtDate,
  };
}

function yAxis(valuesFn: uPlot.Axis['values']): uPlot.Axis {
  const T = getTheme();
  return {
    stroke:    T.label,
    ticks:     { stroke: T.grid, width: 1 },
    grid:      { stroke: T.grid, width: 1 },
    font:      '9px "JetBrains Mono", ui-monospace, monospace',
    labelFont: '9px "JetBrains Mono", ui-monospace, monospace',
    values:    valuesFn,
    size:      40,
  };
}

function baseOpts(
  width: number,
  height: number,
  seriesFn: () => uPlot.Series[],
  axesFn:   () => uPlot.Axis[],
  scales?: Record<string, uPlot.Scale>,
): uPlot.Options {
  return {
    width,
    height,
    padding: CHART_PADDING,
    pxAlign: true,
    cursor: { show: false },
    legend: { show: false },
    series: seriesFn(),
    axes:   axesFn(),
    scales: { x: { time: true }, ...scales },
  };
}

export interface Chart {
  update(data: uPlot.AlignedData): void;
  destroy(): void;
  /** Re-create the plot with current theme colors. */
  recolor(): void;
}

// ─── Helper: create a chart that can re-color on theme change ─────────────────

function makeChart(
  container: HTMLElement,
  w: number,
  h: number,
  buildOpts: (w: number, h: number) => uPlot.Options,
  initData: uPlot.AlignedData,
): Chart {
  let plot = new uPlot(buildOpts(w, h), initData, container);
  let _lastData: uPlot.AlignedData = initData;

  const handler = () => {
    plot.destroy();
    plot = new uPlot(buildOpts(w, h), _lastData, container);
  };
  window.addEventListener('hive-theme-change', handler);

  return {
    update(data) {
      _lastData = data;
      plot.setData(data);
    },
    destroy() {
      window.removeEventListener('hive-theme-change', handler);
      plot.destroy();
    },
    recolor() { handler(); },
  };
}

// ─── Token rate (hive + claude lines) ────────────────────────────────────────
// Title: "TOKEN RATE — hive vs claude"
// Styled to match the tokens-day reference: area fill @1a, stroke 1.5, K/M axis.

export function createTokenRateChart(container: HTMLElement, w: number, h: number): Chart {
  function build(bw: number, bh: number): uPlot.Options {
    const T = getTheme();
    return baseOpts(bw, bh, () => [
      { label: 'time' },
      { label: 'Hive',   stroke: T.green,  width: 1.5, fill: `${T.green}1a`,  spanGaps: true },
      { label: 'Claude', stroke: T.violet, width: 1.5, fill: `${T.violet}1a`, spanGaps: true },
    ], () => [xAxis(), yAxis(fmtTokenVal)]);
  }
  return makeChart(container, w, h, build, [[], [], []]);
}

// ─── Cost ($) ─────────────────────────────────────────────────────────────────
// Title: "COST USD"

export function createCostChart(container: HTMLElement, w: number, h: number): Chart {
  function build(bw: number, bh: number): uPlot.Options {
    const T = getTheme();
    return baseOpts(bw, bh, () => [
      { label: 'time' },
      {
        label:  'Cost $',
        stroke: T.cyan,
        width:  1.5,
        fill:   `${T.cyan}1a`,
        spanGaps: true,
        value:  (_u, v) => (v != null ? `$${v.toFixed(3)}` : '--'),
      },
    ], () => [xAxis(), yAxis(fmtCostVal)]);
  }
  return makeChart(container, w, h, build, [[], []]);
}

// ─── Throughput (done/h) ──────────────────────────────────────────────────────
// Title: "DONE / H"

export function createThroughputChart(container: HTMLElement, w: number, h: number): Chart {
  function build(bw: number, bh: number): uPlot.Options {
    const T = getTheme();
    return baseOpts(bw, bh, () => [
      { label: 'time' },
      { label: 'Done/h', stroke: T.amber, width: 1.5, fill: `${T.amber}1a`, spanGaps: true },
    ], () => [xAxis(), yAxis(fmtRateVal)]);
  }
  return makeChart(container, w, h, build, [[], []]);
}

// ─── Smoke pass % ─────────────────────────────────────────────────────────────
// Title: "SMOKE PASS %"

export function createSmokeChart(container: HTMLElement, w: number, h: number): Chart {
  function build(bw: number, bh: number): uPlot.Options {
    const T = getTheme();
    return baseOpts(bw, bh, () => [
      { label: 'time' },
      {
        label:  'Smoke %',
        stroke: T.green,
        width:  1.5,
        fill:   `${T.green}1a`,
        spanGaps: true,
        value:  (_u, v) => (v != null ? `${Math.round(v)}%` : '--'),
      },
    ], () => [xAxis(), yAxis(fmtPctVal)],
    { y: { range: (_u, _min, _max) => [0, 100] } });
  }
  return makeChart(container, w, h, build, [[], []]);
}

// ─── Parse-fail rate (with alarm colour when > 5%) ───────────────────────────
// Title: "PARSE FAIL %"

export function createParseFailChart(container: HTMLElement, w: number, h: number): Chart {
  function build(bw: number, bh: number): uPlot.Options {
    const T = getTheme();
    return baseOpts(bw, bh, () => [
      { label: 'time' },
      {
        label:  'Fail %',
        stroke: T.red,
        width:  1.5,
        fill:   `${T.red}1a`,
        spanGaps: true,
        value:  (_u, v) => (v != null ? `${(v * 100).toFixed(1)}%` : '--'),
      },
    ], () => [xAxis(), yAxis(fmtPctVal)],
    { y: { range: (_u, _min, _max) => [0, 0.2] } });
  }
  return makeChart(container, w, h, build, [[], []]);
}

// ─── Tokens / day (hive + claude, last 30 days) ───────────────────────────────
// Folded in from the standalone tokens-day panel. Same two-series design with
// MM/DD x-axis and K/M y-axis. Canonical reference styling.
// Title: "TOKENS / DAY"

export function createTokensByDayChart(container: HTMLElement, w: number, h: number): Chart {
  function build(bw: number, bh: number): uPlot.Options {
    const T = getTheme();
    return baseOpts(bw, bh, () => [
      {},  // x (timestamps)
      {
        label:  'Hive',
        stroke: T.green,
        fill:   `${T.green}1a`,
        width:  1.5,
      },
      {
        label:  'Claude',
        stroke: T.cyan,
        fill:   `${T.cyan}1a`,
        width:  1.5,
      },
    ], () => [xAxis(), yAxis(fmtTokenVal)]);
  }
  return makeChart(container, w, h, build, [[], [], []]);
}

// ─── GPU util sparkline (single card) ────────────────────────────────────────

export function createGpuSparkline(container: HTMLElement, w: number, h: number): Chart {
  function build(bw: number, bh: number): uPlot.Options {
    const T = getTheme();
    return baseOpts(bw, bh, () => [
      { label: 'time' },
      { label: 'Util%', stroke: T.copper, width: 1.5, spanGaps: true },
    ], () => [
      { ...xAxis(), show: false },
      { ...yAxis(fmtPctVal), show: false },
    ], { y: { range: (_u, _min, _max) => [0, 100] } });
  }
  return makeChart(container, w, h, build, [[], []]);
}
