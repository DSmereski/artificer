/**
 * layout/freeform.ts — Free-form drag/resize logic for P1 (Spine B).
 *
 * Pure geometry utilities — no DOM, no state mutations. All functions are
 * deterministic given their inputs; unit-testable in a Node environment.
 *
 * What lives here:
 *   - Grid-snap helper (`snapToGrid`).
 *   - Hit-testing for resize handles on a panel rect (`hitTestHandle`).
 *   - Drag-delta application: move + resize on each of the 8 handle zones.
 *   - Bounds clamping so panels stay within a viewport rect.
 *
 * The DOM interaction layer (pointer events, cursor changes) lives in
 * `freeform-apply.ts` and calls these functions.
 */

import type { InstanceGeometry } from '../plugins/instances.js';

// ─── Grid ──────────────────────────────────────────────────────────────────────

export const DEFAULT_GRID_SIZE = 20; // px — light snap

/** Snap a coordinate to the nearest grid point. */
export function snapToGrid(v: number, grid: number = DEFAULT_GRID_SIZE): number {
  return Math.round(v / grid) * grid;
}

// ─── Handle zones ──────────────────────────────────────────────────────────────

/** The 8 resize handle positions + the body (for move). */
export type HandleZone =
  | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w' | 'nw'
  | 'body'
  | 'none';

/** px inset from the panel edge used as the handle hit area. */
const HANDLE_INSET = 16; // fat resize edges — easy to grab (was 10, too thin)

/**
 * Given a pointer position `(px, py)` relative to the panel's top-left corner
 * and the panel's dimensions `(pw, ph)`, return which handle zone was hit.
 *
 * 'none' is returned only when the pointer is outside the panel rect entirely.
 * Corners > edges > body priority (corners are checked first).
 */
export function hitTestHandle(
  px: number,
  py: number,
  pw: number,
  ph: number,
): HandleZone {
  if (px < 0 || py < 0 || px > pw || py > ph) return 'none';

  const nearTop    = py <= HANDLE_INSET;
  const nearBottom = py >= ph - HANDLE_INSET;
  const nearLeft   = px <= HANDLE_INSET;
  const nearRight  = px >= pw - HANDLE_INSET;

  // Corners first (highest priority — smallest target area)
  if (nearTop    && nearLeft)  return 'nw';
  if (nearTop    && nearRight) return 'ne';
  if (nearBottom && nearLeft)  return 'sw';
  if (nearBottom && nearRight) return 'se';

  // Edges
  if (nearTop)    return 'n';
  if (nearBottom) return 's';
  if (nearLeft)   return 'w';
  if (nearRight)  return 'e';

  return 'body';
}

// ─── CSS cursor mapping ────────────────────────────────────────────────────────

/** Map a handle zone to the appropriate CSS cursor string. */
export function cursorForZone(zone: HandleZone): string {
  switch (zone) {
    case 'n':  case 's':  return 'ns-resize';
    case 'e':  case 'w':  return 'ew-resize';
    case 'ne': case 'sw': return 'nesw-resize';
    case 'nw': case 'se': return 'nwse-resize';
    case 'body':           return 'move';
    default:               return 'default';
  }
}

// ─── Minimum dimensions ────────────────────────────────────────────────────────

export const MIN_W = 80;   // px
export const MIN_H = 60;   // px

// ─── Apply a drag delta ────────────────────────────────────────────────────────

/**
 * Apply a drag delta `(dx, dy)` to geometry `g` for the given handle zone.
 * Returns a new geometry object (immutable — the original is never mutated).
 * Applies grid-snap and enforces MIN_W / MIN_H.
 */
export function applyDelta(
  g: InstanceGeometry,
  zone: HandleZone,
  dx: number,
  dy: number,
  grid: number = DEFAULT_GRID_SIZE,
): InstanceGeometry {
  let { x, y, w, h } = g;

  switch (zone) {
    case 'body':
      x += dx;
      y += dy;
      break;
    case 'n':
      y += dy;
      h -= dy;
      break;
    case 's':
      h += dy;
      break;
    case 'e':
      w += dx;
      break;
    case 'w':
      x += dx;
      w -= dx;
      break;
    case 'ne':
      y += dy;
      h -= dy;
      w += dx;
      break;
    case 'nw':
      x += dx;
      y += dy;
      w -= dx;
      h -= dy;
      break;
    case 'se':
      w += dx;
      h += dy;
      break;
    case 'sw':
      x += dx;
      w -= dx;
      h += dy;
      break;
    case 'none':
      return g;
  }

  // Enforce minimums (must happen before snap so snap never pushes below min)
  if (w < MIN_W) {
    // Anchor the opposite edge when resizing from left
    if (zone === 'w' || zone === 'nw' || zone === 'sw') {
      x = x + (w - MIN_W); // undo the x move that brought w below min
    }
    w = MIN_W;
  }
  if (h < MIN_H) {
    if (zone === 'n' || zone === 'nw' || zone === 'ne') {
      y = y + (h - MIN_H);
    }
    h = MIN_H;
  }

  // Snap to grid
  x = snapToGrid(x, grid);
  y = snapToGrid(y, grid);
  w = snapToGrid(w, grid);
  h = snapToGrid(h, grid);

  // After snap, enforce minimums again (snap may have brought w/h below)
  w = Math.max(MIN_W, w);
  h = Math.max(MIN_H, h);

  return { x, y, w, h };
}

// ─── Viewport clamping ─────────────────────────────────────────────────────────

/**
 * Clamp a geometry so the panel is at least partially visible inside the
 * viewport rect `(0, 0, vw, vh)`.
 * "At least partially visible" means at least MARGIN px of the panel must
 * remain on-screen on each axis.
 */
const CLAMP_MARGIN = 40; // px

export function clampToViewport(
  g: InstanceGeometry,
  vw: number,
  vh: number,
): InstanceGeometry {
  const x = Math.min(Math.max(g.x, CLAMP_MARGIN - g.w), vw - CLAMP_MARGIN);
  const y = Math.min(Math.max(g.y, 0), vh - CLAMP_MARGIN);
  const w = Math.min(g.w, vw);
  const h = Math.min(g.h, vh);
  return { x, y, w, h };
}

// ─── Default geometry helper ───────────────────────────────────────────────────

/** Default size when creating a new instance in free-form mode. */
export const DEFAULT_INSTANCE_W = 400;
export const DEFAULT_INSTANCE_H = 300;

/**
 * Generate a non-overlapping default position for a new panel.
 * Each successive call offsets by one grid step to cascade new panels.
 */
let _cascadeOffset = 0;

export function defaultGeometry(grid: number = DEFAULT_GRID_SIZE): InstanceGeometry {
  const offset = _cascadeOffset * grid * 2;
  _cascadeOffset = (_cascadeOffset + 1) % 10; // wrap after 10
  return {
    x: snapToGrid(60 + offset, grid),
    y: snapToGrid(80 + offset, grid),
    w: DEFAULT_INSTANCE_W,
    h: DEFAULT_INSTANCE_H,
  };
}

/** Reset the cascade counter (for tests). */
export function _resetCascadeForTest(): void {
  _cascadeOffset = 0;
}
