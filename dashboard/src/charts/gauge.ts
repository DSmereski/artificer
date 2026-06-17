/**
 * gauge.ts — Hand-drawn canvas arc gauges (no WebGL, no lib).
 *
 * Draws a single arc gauge on a 2D canvas context.
 * Designed to be called in requestAnimationFrame loops or on data update.
 */

// Colour tokens (hex, since canvas doesn't support OKLCH).
const C_BG_ARC   = '#1c201a';
const C_TRACK    = '#363c30';
const C_COPPER   = '#c07840';
const C_CYAN     = '#60c8c8';
const C_DIM      = '#b8b4a8';
const C_FAINT    = '#8a8780';
const C_INK      = '#f2f0ec';

/**
 * Interpolate a colour between copper → amber → red based on 0-100 temp.
 *   0-50°C: copper
 *   50-80°C: copper → amber
 *   80-100°C: amber → red
 */
function tempColour(tempC: number): string {
  if (tempC <= 50) return C_COPPER;
  if (tempC <= 80) {
    // lerp copper→amber
    const t = (tempC - 50) / 30;
    return lerpColour([0xc0, 0x78, 0x40], [0xe0, 0xa0, 0x30], t);
  }
  // lerp amber→red
  const t = Math.min(1, (tempC - 80) / 20);
  return lerpColour([0xe0, 0xa0, 0x30], [0xc4, 0x40, 0x40], t);
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
  ctx.fillStyle = C_BG_ARC;
  ctx.fill();

  if (opts.noSignal) {
    // No signal state — draw dim track + text
    _drawArcTrack(ctx, cx, cy, r, strokeW, C_TRACK);
    ctx.font = `bold ${Math.round(r * 0.28)}px "JetBrains Mono", monospace`;
    ctx.fillStyle = C_FAINT;
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
  _drawArcTrack(ctx, cx, cy, r, strokeW, C_TRACK);

  // Outer arc fill (utilization)
  _drawArcFill(ctx, cx, cy, r, strokeW, fillPct / 100, arcColour);

  // Inner ring (vram) — slightly smaller radius
  if (vramPct !== null) {
    const rInner = r - strokeW - 4;
    if (rInner > 4) {
      _drawArcTrack(ctx, cx, cy, rInner, strokeW * 0.5, C_TRACK);
      _drawArcFill(ctx, cx, cy, rInner, strokeW * 0.5, vramPct / 100, C_CYAN);
    }
  }

  // Centre label
  if (opts.centerLabel) {
    const fontSize = Math.round(r * 0.30);
    ctx.font = `bold ${fontSize}px "JetBrains Mono", monospace`;
    ctx.fillStyle = C_INK;
    ctx.textAlign = 'center';
    ctx.textBaseline = opts.centerSub ? 'alphabetic' : 'middle';
    const yOff = opts.centerSub ? cy - fontSize * 0.1 : cy;
    ctx.fillText(opts.centerLabel, cx, yOff);
  }

  if (opts.centerSub) {
    const subSize = Math.round(r * 0.17);
    ctx.font = `${subSize}px Inter, sans-serif`;
    ctx.fillStyle = C_DIM;
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
    ctx.strokeStyle = C_TRACK;
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
