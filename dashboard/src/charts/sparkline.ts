/**
 * charts/sparkline.ts — tiny canvas trend line (DASH it6).
 *
 * Pure, dependency-free. Draws a min/max-normalised line with a soft area
 * fill into a small canvas. Used by the KPI tiles and metric panels to show
 * where a number has been heading.
 */

export interface SparkOpts {
  color?: string;       // stroke (CSS color)
  fill?: boolean;       // draw soft area under the line
  width?: number;       // line width
}

/** Push a value onto a capped ring-buffer history (newest last). */
export function pushHist(buf: number[], v: number, cap = 48): number[] {
  buf.push(Number.isFinite(v) ? v : 0);
  if (buf.length > cap) buf.shift();
  return buf;
}

export function drawSparkline(
  canvas: HTMLCanvasElement,
  vals: readonly number[],
  opts: SparkOpts = {},
): void {
  const g = canvas.getContext('2d');
  if (!g) return;
  const W = canvas.width, H = canvas.height;
  g.clearRect(0, 0, W, H);
  if (vals.length < 2) return;

  const color = opts.color ?? '#c17f24';
  const lw = opts.width ?? 1.5;
  let max = -Infinity, min = Infinity;
  for (const v of vals) { if (v > max) max = v; if (v < min) min = v; }
  const range = max - min || 1;
  const pad = lw + 0.5;

  const xy = (i: number, v: number): [number, number] => {
    const x = (i / (vals.length - 1)) * W;
    const y = H - pad - ((v - min) / range) * (H - pad * 2);
    return [x, y];
  };

  g.beginPath();
  vals.forEach((v, i) => {
    const [x, y] = xy(i, v);
    if (i === 0) g.moveTo(x, y); else g.lineTo(x, y);
  });
  g.strokeStyle = color;
  g.lineWidth = lw;
  g.lineJoin = 'round';
  g.stroke();

  if (opts.fill !== false) {
    g.lineTo(W, H);
    g.lineTo(0, H);
    g.closePath();
    g.globalAlpha = 0.12;
    g.fillStyle = color;
    g.fill();
    g.globalAlpha = 1;
  }
}
