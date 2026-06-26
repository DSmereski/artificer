/**
 * gauge.ts — Hand-drawn canvas arc gauges (no WebGL, no lib).
 *
 * Draws a single arc gauge on a 2D canvas context.
 * Designed to be called in requestAnimationFrame loops or on data update.
 * All colors are resolved from CSS theme vars at draw-time so gauges
 * automatically re-color when the dashboard theme switches.
 */

// ─── CSS-var reader ───────────────────────────────────────────────────────────

function cssHex(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function gaugeColors() {
  return {
    bgArc:  cssHex('--hex-card',   '#1c201a'),
    track:  cssHex('--hex-line',   '#363c30'),
    copper: cssHex('--hex-copper', '#c07840'),
    amber:  cssHex('--hex-amber',  '#e0a030'),
    red:    cssHex('--hex-red',    '#c44040'),
    cyan:   cssHex('--hex-cyan',   '#60c8c8'),
    dim:    cssHex('--hex-dim',    '#b8b4a8'),
    faint:  cssHex('--hex-faint',  '#8a8780'),
    ink:    cssHex('--hex-ink',    '#f2f0ec'),
  };
}

/**
 * Interpolate a colour between copper → amber → red based on 0-100 temp.
 *   0-50°C: copper
 *   50-80°C: copper → amber
 *   80-100°C: amber → red
 */
function tempColour(tempC: number): string {
  const c = gaugeColors();
  const copperRgb = hexToRgb(c.copper) ?? [0xc0, 0x78, 0x40];
  const amberRgb  = hexToRgb(c.amber)  ?? [0xe0, 0xa0, 0x30];
  const redRgb    = hexToRgb(c.red)    ?? [0xc4, 0x40, 0x40];

  if (tempC <= 50) return c.copper;
  if (tempC <= 80) {
    const t = (tempC - 50) / 30;
    return lerpColour(copperRgb, amberRgb, t);
  }
  const t = Math.min(1, (tempC - 80) / 20);
  return lerpColour(amberRgb, redRgb, t);
}

function hexToRgb(hex: string): number[] | null {
  const m = hex.replace('#', '').match(/.{2}/g);
  if (!m || m.length < 3) return null;
  return m.slice(0, 3).map(x => parseInt(x, 16));
}

function lerpColour(a: number[], b: number[], t: number): string {
  const r = Math.round(a[0] + (b[0] - a[0]) * t);
  const g = Math.round(a[1] + (b[1] - a[1]) * t);
  const bl = Math.round(a[2] + (b[2] - a[2]) * t);
  return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${bl.toString(16).padStart(2, '0')}`;
}

export interface GaugeOpts {
  /** 0-100 fill percentage for the main arc (utilization). */
  fillPct: number;
  /** 0-100 percentage for the inner ring (vram). */
  vramPct?: number;
  /** Temperature in Celsius (controls colour). */
  tempC?: number;
  /** Label shown in the centre (e.g. "73%"). */
  centerLabel?: string;
  /** Sub-label below centre (e.g. "util"). */
  centerSub?: string;
  /** If true, render in "no signal" state. */
  noSignal?: boolean;
}

/**
 * Draw a gauge onto a canvas element.
 * The canvas must already be sized (width/height attributes set).
 */
export function drawGauge(canvas: HTMLCanvasElement, opts: GaugeOpts): void {
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const C = gaugeColors();
  const w = canvas.width;
  const h = canvas.height;
  const cx = w / 2;
  const cy = h / 2;
  const r = Math.min(w, h) * 0.38;
  const strokeW = Math.max(6, r * 0.14);

  ctx.clearRect(0, 0, w, h);

  // Background circle
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.fillStyle = C.bgArc;
  ctx.fill();

  if (opts.noSignal) {
    // No signal state — draw dim track + text
    _drawArcTrack(ctx, cx, cy, r, strokeW, C.track);
    ctx.font = `bold ${Math.round(r * 0.28)}px "JetBrains Mono", monospace`;
    ctx.fillStyle = C.faint;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('–', cx, cy);
    return;
  }

  const fillPct  = Math.max(0, Math.min(100, opts.fillPct));
  const vramPct  = opts.vramPct != null ? Math.max(0, Math.min(100, opts.vramPct)) : null;
  const tempC    = opts.tempC ?? 0;
  const arcColour = tempColour(tempC);

  // Outer track (grey background arc)
  _drawArcTrack(ctx, cx, cy, r, strokeW, C.track);

  // Outer arc fill (utilization)
  _drawArcFill(ctx, cx, cy, r, strokeW, fillPct / 100, arcColour);

  // Inner ring (vram) — slightly smaller radius
  if (vramPct !== null) {
    const rInner = r - strokeW - 4;
    if (rInner > 4) {
      _drawArcTrack(ctx, cx, cy, rInner, strokeW * 0.5, C.track);
      _drawArcFill(ctx, cx, cy, rInner, strokeW * 0.5, vramPct / 100, C.cyan);
    }
  }

  // Centre label
  if (opts.centerLabel) {
    const fontSize = Math.round(r * 0.30);
    ctx.font = `bold ${fontSize}px "JetBrains Mono", monospace`;
    ctx.fillStyle = C.ink;
    ctx.textAlign = 'center';
    ctx.textBaseline = opts.centerSub ? 'alphabetic' : 'middle';
    const yOff = opts.centerSub ? cy - fontSize * 0.1 : cy;
    ctx.fillText(opts.centerLabel, cx, yOff);
  }

  if (opts.centerSub) {
    const subSize = Math.round(r * 0.17);
    ctx.font = `${subSize}px Inter, sans-serif`;
    ctx.fillStyle = C.dim;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ctx.fillText(opts.centerSub, cx, cy + Math.round(r * 0.04));
  }

  // Temperature indicator (small arc at the bottom)
  if (opts.tempC != null) {
    const tempPct = Math.min(1, opts.tempC / 100);
    const tR = r * 0.55;
    const tSW = strokeW * 0.35;
    // mini arc below centre
    ctx.beginPath();
    const startA = Math.PI * 0.65;
    const endA   = Math.PI * 1.35;
    ctx.arc(cx, cy + r * 0.22, tR * 0.4, startA, endA);
    ctx.strokeStyle = C.track;
    ctx.lineWidth   = tSW;
    ctx.lineCap     = 'round';
    ctx.stroke();

    const sweep = (endA - startA) * tempPct;
    ctx.beginPath();
    ctx.arc(cx, cy + r * 0.22, tR * 0.4, startA, startA + sweep);
    ctx.strokeStyle = arcColour;
    ctx.lineWidth   = tSW;
    ctx.lineCap     = 'round';
    ctx.stroke();
  }
}

// ─── Arc drawing helpers ──────────────────────────────────────────────────────

const ARC_START = -Math.PI * 0.75; // start at 7 o'clock position
const ARC_SWEEP = Math.PI * 1.5;   // 270 degree sweep

function _drawArcTrack(
  ctx: CanvasRenderingContext2D,
  cx: number, cy: number, r: number, sw: number,
  colour: string,
): void {
  ctx.beginPath();
  ctx.arc(cx, cy, r, ARC_START, ARC_START + ARC_SWEEP);
  ctx.strokeStyle = colour;
  ctx.lineWidth   = sw;
  ctx.lineCap     = 'round';
  ctx.stroke();
}

function _drawArcFill(
  ctx: CanvasRenderingContext2D,
  cx: number, cy: number, r: number, sw: number,
  fraction: number, // 0-1
  colour: string,
): void {
  if (fraction <= 0) return;
  ctx.beginPath();
  ctx.arc(cx, cy, r, ARC_START, ARC_START + ARC_SWEEP * fraction);
  ctx.strokeStyle = colour;
  ctx.lineWidth   = sw;
  ctx.lineCap     = 'round';
  ctx.stroke();
}
