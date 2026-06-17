/**
 * layout/engine.ts — Weight-driven cell tiler for the adaptive dashboard.
 *
 * Given a list of plugins + current SystemState, computes a layout:
 * each plugin gets a {x,y,w,h,sizeClass} rect on the 5120×1440 canvas.
 *
 * Algorithm:
 *   1. Call every plugin's relevance(state); drop priority<10 / 'hidden'.
 *   2. Map SizeHint → base span; apply weight multiplier.
 *   3. Stable-sort by priority (desc); hero gets a guaranteed min band.
 *   4. Bin-pack into horizontal bands across 5120×1440.
 *      GUARANTEE: every panel that passes the relevance filter gets a seat.
 *      Extra bands are added until all panels are placed.
 *   5. Return layout diff for the DOM applier.
 *
 * Pure function — no DOM, no side effects. Unit-testable.
 */

import type { SystemState, SizeHint } from '../state/types.js';
import type { PanelPlugin, RelevanceResult, Rect } from '../plugins/contract.js';

// ─── Grid constants ───────────────────────────────────────────────────────────

export const VIEWPORT_W = 5120;
export const VIEWPORT_H = 1440;
const TOPBAR_H = 56;
const CONTENT_H = VIEWPORT_H - TOPBAR_H;
const GRID_COLS = 12; // 12-wide hex-aligned grid per band
const GUTTER    = 8;  // px between cells

// ─── Size hint → column spans ────────────────────────────────────────────────

const SIZE_SPANS: Record<SizeHint, number> = {
  hero:   12,
  lg:      6,
  md:      4,
  sm:      3,
  min:     2,
  hidden:  0,
};

/**
 * Minimum column span for any visible panel.  Prevents content panels
 * (especially Telemetry with uPlot charts) from being squeezed below a
 * usable width when the packer truncates the last column slot in a band.
 */
const MIN_PANEL_COLS = 2;

/**
 * Telemetry-specific minimum: uPlot needs at least this many cols to render
 * its sparklines without cramping.
 */
const TELEMETRY_MIN_COLS = 4;
const TELEMETRY_ID = 'telemetry';

// ─── Band height fractions — 3 bands ─────────────────────────────────────────
//
// Three equal bands each ~460 px tall at 1440px height (minus 56px topbar).
// That gives ~446px per band — spacious enough for every panel type.
// The first band gets a slightly larger slice so the hero / crew-board
// area has more breathing room.

const BAND_HEIGHT_FRACTIONS: number[] = [0.4, 0.33, 0.27]; // must sum to 1.0

// ─── Layout result ────────────────────────────────────────────────────────────

export interface CellLayout {
  id: string;
  rect: Rect;
  sizeClass: SizeHint;
  priority: number;
  /** CSS grid-column span (1–12). Used by the CSS Grid applier. */
  colSpan: number;
  /** Grid row index: 0 = top band, 1 = middle band, 2 = bottom band. */
  bandIdx: number;
  /** Column start position within the band (0-based). */
  colStart: number;
}

export interface LayoutResult {
  cells: CellLayout[];
  /** IDs that were visible before and are now hidden (for suspend). */
  nowHidden: string[];
  /** IDs that are new or newly visible (for resume/mount). */
  nowVisible: string[];
}

// ─── Placed plugin ────────────────────────────────────────────────────────────

interface Ranked {
  plugin: PanelPlugin;
  rel: RelevanceResult;
  spans: number;
}

// ─── Main layout function ─────────────────────────────────────────────────────

/**
 * Compute a full layout from plugins + state.
 * prev is the last committed layout (for diff computation).
 */
export function computeLayout(
  plugins: PanelPlugin[],
  state: SystemState,
  prev: CellLayout[],
  focusId: string | null = null,
): LayoutResult {
  const prevIds = new Set(prev.map((c) => c.id));

  // 1. Rank plugins — filter hidden / low-priority
  const ranked: Ranked[] = [];
  for (const plugin of plugins) {
    // CC5 focus mode: when a panel is focused, it takes the whole grid and
    // every other panel is hidden (restored on un-focus).
    let rel: RelevanceResult;
    if (focusId) {
      rel = plugin.id === focusId
        ? { priority: 100, size: 'hero', weight: 2 }
        : { priority: 0, size: 'hidden' };
    } else {
      // CC7 error boundary: a panel that throws in relevance() must not break
      // the whole layout — treat it as hidden and carry on.
      try {
        rel = plugin.relevance(state);
      } catch (err) {
        console.error(`[layout] ${plugin.id}.relevance threw:`, err);
        continue;
      }
    }
    if (rel.size === 'hidden' || rel.priority < 10) continue;
    const baseSpan = SIZE_SPANS[rel.size];
    if (baseSpan === 0) continue;
    const weight = rel.weight ?? 1;
    // Enforce per-plugin minimums before clamping to grid width.
    const minCols = plugin.id === TELEMETRY_ID ? TELEMETRY_MIN_COLS : MIN_PANEL_COLS;
    const spans = Math.max(minCols, Math.min(GRID_COLS, Math.round(baseSpan * weight)));
    ranked.push({ plugin, rel, spans });
  }

  // 2. Stable sort by priority desc
  ranked.sort((a, b) => {
    const dp = b.rel.priority - a.rel.priority;
    if (dp !== 0) return dp;
    // Stable: preserve insertion order
    return plugins.indexOf(a.plugin) - plugins.indexOf(b.plugin);
  });

  // 3. Hero guarantee: if any plugin wants 'hero', move it to front
  const heroIdx = ranked.findIndex((r) => r.rel.size === 'hero');
  if (heroIdx > 0) {
    const [hero] = ranked.splice(heroIdx, 1);
    ranked.unshift(hero);
  }

  // 4. Bin-pack into bands
  //    We use as many bands as needed to seat every ranked panel.
  //    Pre-compute the y-positions of all bands (start with 3 standard bands;
  //    add more at equal heights if we overflow).

  const cells: CellLayout[] = [];

  // Compute standard band y-offsets and heights.
  function buildBands(count: number): Array<{ y: number; h: number }> {
    if (count <= BAND_HEIGHT_FRACTIONS.length) {
      // Use the predefined fractions for the first N bands.
      const fracs = BAND_HEIGHT_FRACTIONS.slice(0, count);
      const total = fracs.reduce((s, f) => s + f, 0);
      const normalised = fracs.map((f) => f / total);
      let yAcc = TOPBAR_H;
      return normalised.map((f) => {
        const h = Math.round(CONTENT_H * f);
        const band = { y: yAcc, h };
        yAcc += h;
        return band;
      });
    }
    // More bands than fractions: divide the content area equally.
    const h = Math.floor(CONTENT_H / count);
    return Array.from({ length: count }, (_, i) => ({
      y: TOPBAR_H + i * h,
      h,
    }));
  }

  // Determine the minimum number of bands needed to fit all panels.
  // We pack greedily band-by-band and count how many bands we need.
  function neededBands(): number {
    let bands = 1;
    let col = 0;
    let panelIdx = 0;
    while (panelIdx < ranked.length) {
      const avail = GRID_COLS - col;
      const entry = ranked[panelIdx];
      const used = Math.min(entry.spans, avail);
      if (used < MIN_PANEL_COLS && avail < MIN_PANEL_COLS) {
        // Overflow to next band.
        bands++;
        col = 0;
        continue;
      }
      col += used;
      panelIdx++;
      if (col >= GRID_COLS) {
        col = 0;
        if (panelIdx < ranked.length) bands++;
      }
    }
    return bands;
  }

  const numBands = Math.max(1, neededBands());
  const bands = buildBands(numBands);

  let pluginIndex = 0;
  const colW = Math.round((VIEWPORT_W - GUTTER * (GRID_COLS + 1)) / GRID_COLS);

  for (let bandIdx = 0; bandIdx < bands.length; bandIdx++) {
    const band = bands[bandIdx];
    let colCursor = 0;

    while (pluginIndex < ranked.length && colCursor < GRID_COLS) {
      const entry = ranked[pluginIndex];
      const availCols = GRID_COLS - colCursor;

      // If the panel's natural span doesn't fit, truncate to available space.
      // But always honour the per-panel minimum.
      const minCols = entry.plugin.id === TELEMETRY_ID ? TELEMETRY_MIN_COLS : MIN_PANEL_COLS;
      let spanUsed = Math.min(entry.spans, availCols);

      if (spanUsed < minCols) {
        // Not enough columns left in this band — overflow to next band.
        break;
      }

      const x = GUTTER + colCursor * (colW + GUTTER);
      const w = spanUsed * colW + (spanUsed - 1) * GUTTER;
      const y = band.y + GUTTER;
      const h = band.h - GUTTER * 2;

      cells.push({
        id: entry.plugin.id,
        rect: { x, y, w, h },
        sizeClass: entry.rel.size,
        priority: entry.rel.priority,
        colSpan: spanUsed,
        bandIdx,
        colStart: colCursor,
      });

      colCursor += spanUsed;
      pluginIndex++;
    }
  }

  // 4b. Stretch each band to fill the FULL grid width — no leftover empty
  //     columns (those read as ugly black voids). Distribute the unused columns
  //     across the band's panels round-robin, then re-flow colStart. Also widen
  //     the pixel rect so any non-grid consumer stays consistent.
  const byBand = new Map<number, CellLayout[]>();
  for (const c of cells) {
    const arr = byBand.get(c.bandIdx);
    if (arr) arr.push(c); else byBand.set(c.bandIdx, [c]);
  }
  for (const bandCells of byBand.values()) {
    const used = bandCells.reduce((s, c) => s + c.colSpan, 0);
    let leftover = GRID_COLS - used;
    let i = 0;
    while (leftover > 0) {
      bandCells[i % bandCells.length].colSpan += 1;
      leftover--;
      i++;
    }
    let cur = 0;
    for (const c of bandCells) {
      c.colStart = cur;
      c.rect = {
        ...c.rect,
        x: GUTTER + cur * (colW + GUTTER),
        w: c.colSpan * colW + (c.colSpan - 1) * GUTTER,
      };
      cur += c.colSpan;
    }
  }

  // 5. Compute diff
  const newIds    = new Set(cells.map((c) => c.id));
  const nowHidden  = prev.filter((c) => !newIds.has(c.id)).map((c) => c.id);
  const nowVisible = cells.filter((c) => !prevIds.has(c.id)).map((c) => c.id);

  return { cells, nowHidden, nowVisible };
}
