/**
 * format.ts — Pure formatting helpers + rolling-buffer logic.
 *
 * All functions are pure (no DOM/side effects) so they are unit-testable
 * without a browser environment.
 */

import type { BoardStatsSample } from './types.js';

// ─── Number / token formatting ────────────────────────────────────────────────

/**
 * Format a large integer with K/M suffix.
 *   fmtNum(1234)      → "1.2k"
 *   fmtNum(1_200_000) → "1.2M"
 */
export function fmtNum(n: number): string {
  if (!isFinite(n)) return '--';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000)     return `${(n / 1_000).toFixed(1)}k`;
  return String(Math.round(n));
}

/**
 * Format a USD cost value.
 *   fmtCost(0)        → "$0.00"
 *   fmtCost(12.5678)  → "$12.57"
 *   fmtCost(null)     → "--"
 */
export function fmtCost(usd: number | null | undefined): string {
  if (usd == null || !isFinite(usd)) return '--';
  return `$${usd.toFixed(2)}`;
}

/**
 * Format a percentage (0-100 range).
 *   fmtPct(87.4)  → "87%"
 *   fmtPct(100)   → "100%"
 */
export function fmtPct(pct: number | null | undefined): string {
  if (pct == null || !isFinite(pct)) return '--%';
  return `${Math.round(pct)}%`;
}

/**
 * Format a 0-1 rate as a percentage string.
 *   fmtRate(0.034) → "3.4%"
 */
export function fmtRate(rate: number | null | undefined): string {
  if (rate == null || !isFinite(rate)) return '--%';
  return `${(rate * 100).toFixed(1)}%`;
}

/**
 * Format seconds as human-readable duration.
 *   fmtDuration(61)     → "1m 1s"
 *   fmtDuration(3665)   → "1h 1m"
 *   fmtDuration(90000)  → "25h 0m"
 */
export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null || !isFinite(seconds) || seconds < 0) return '--';
  const s = Math.floor(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

/**
 * Format an ISO timestamp to a short wall-clock string.
 *   fmtTime("2026-06-13T14:23:00Z") → "14:23"
 */
export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '--';
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '--';
    return d.toLocaleTimeString('en-US', {
      hour12: false,
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return '--';
  }
}

/**
 * Format an ISO timestamp as relative time.
 *   fmtRelative(now - 70_000) → "1m ago"
 *   fmtRelative(now - 3700_000) → "1h ago"
 */
export function fmtRelative(isoOrMs: string | number | null | undefined): string {
  if (isoOrMs == null) return '--';
  let ms: number;
  if (typeof isoOrMs === 'string') {
    const d = new Date(isoOrMs);
    if (isNaN(d.getTime())) return '--';
    ms = d.getTime();
  } else {
    ms = isoOrMs;
  }
  const diffS = Math.floor((Date.now() - ms) / 1000);
  if (diffS < 0)    return 'just now';
  if (diffS < 60)   return `${diffS}s ago`;
  const diffM = Math.floor(diffS / 60);
  if (diffM < 60)   return `${diffM}m ago`;
  const diffH = Math.floor(diffM / 60);
  return `${diffH}h ago`;
}

// ─── GPU label helper ─────────────────────────────────────────────────────────

/**
 * Return a short display name for a GPU.
 * Detects the two 5060 Ti cards and labels them GPU 1 / GPU 2.
 * The 4080 is labelled "RTX 4080 (gaming)".
 */
export function gpuLabel(name: string, index: number): string {
  const n = name.toUpperCase();
  if (n.includes('4080')) return 'RTX 4080 (gaming)';
  if (n.includes('5060')) return `GPU ${index + 1} — RTX 5060 Ti`;
  return `GPU ${index + 1} — ${name}`;
}

// ─── Rolling buffer ───────────────────────────────────────────────────────────

const ROLLING_BUFFER_MAX = 120; // ~20 min at 10s poll

/**
 * Push a new sample into the rolling buffer, capping at ROLLING_BUFFER_MAX.
 * Pure: returns a new array, does not mutate the input.
 */
export function pushSample(
  buf: BoardStatsSample[],
  sample: BoardStatsSample,
): BoardStatsSample[] {
  const next = [...buf, sample];
  if (next.length > ROLLING_BUFFER_MAX) {
    return next.slice(next.length - ROLLING_BUFFER_MAX);
  }
  return next;
}

/**
 * Extract two parallel arrays (timestamps, values) from the rolling buffer
 * suitable for uPlot data format: [[ts...], [values...]].
 *
 * uPlot expects timestamps in unix *seconds* (not ms).
 */
export function bufferToUplot(
  buf: BoardStatsSample[],
  getter: (s: BoardStatsSample) => number,
): [number[], number[]] {
  const ts: number[] = [];
  const vals: number[] = [];
  for (const s of buf) {
    ts.push(s.ts / 1000); // ms → seconds
    vals.push(getter(s));
  }
  return [ts, vals];
}

/**
 * Compute done/hour throughput from the rolling buffer.
 * Looks at the window from buf[0] to buf[last] and computes
 * (deltaTasksDone / windowHours).
 * Returns 0 if fewer than 2 samples or window is 0.
 */
export function throughputPerHour(buf: BoardStatsSample[]): number {
  if (buf.length < 2) return 0;
  const first = buf[0];
  const last  = buf[buf.length - 1];
  const windowHours = (last.ts - first.ts) / (1000 * 3600);
  if (windowHours <= 0) return 0;
  const delta = Math.max(0, last.done_count - first.done_count);
  return delta / windowHours;
}

// ─── HTML escape (used by all render functions) ───────────────────────────────

/**
 * Escape HTML special characters to prevent XSS from task titles/slugs.
 */
export function escHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
