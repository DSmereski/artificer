/**
 * layout/desktop.ts — Desktop-style windowed layout (DL2-DL4).
 *
 * Each panel becomes a floating window: draggable header, resizable edges/corners.
 * Activated via localStorage flag `dash:desktopMode=1`.
 *
 * Design constraints:
 *   - Mouse/pointer only (Lively forwards mouse; keyboard unreliable).
 *   - Geometry persisted to `dash:desktopRects` (panelId → {x,y,w,h}).
 *   - Lock flag in `dash:desktopLocked` freezes move/resize until unlocked.
 *   - `saveDesktopRects()` called on pagehide (DL4 persist-on-exit).
 *   - `getDesktopCellEl(id)` lets main.ts's update loop find the element.
 *
 * Reuses pure geometry helpers from freeform.ts (hitTestHandle, applyDelta,
 * clampToViewport) so the math is battle-tested.
 */

import type { PanelPlugin } from '../plugins/contract.js';
import type { SystemState } from '../state/types.js';
import type { InstanceGeometry } from '../plugins/instances.js';
import {
  hitTestHandle,
  cursorForZone,
  applyDelta,
  clampToViewport,
  DEFAULT_GRID_SIZE,
  type HandleZone,
} from './freeform.js';
import {
  isModuleEnabledForLayout,
  initLayoutModulesFromGlobal,
} from './layout-modules.js';
import { isPanelVisible, registerCellEl } from './apply.js';
import { isPanelEnabled } from '../plugins/registry.js';

// ─── Feature flag ─────────────────────────────────────────────────────────────

const LS_MODE    = 'dash:desktopMode';
const LS_RECTS   = 'dash:desktopRects';
const LS_LOCKED  = 'dash:desktopLocked';
const LS_Z       = 'dash:desktopZ';

// Per-monitor layout: the wallpaper host opens each monitor's window with a
// ?win=<index> param. The desktop LAYOUT (rects + z-order) is namespaced by it
// so every monitor keeps its own arrangement; MODE + LOCKED stay global (one
// switch flips all monitors into desktop mode). Reads fall back to the legacy
// un-suffixed key, so an existing single-window layout seeds each monitor on
// first run instead of starting blank.
const WIN: string = (() => {
  try { return new URLSearchParams(location.search).get('win') ?? ''; }
  catch { return ''; }
})();
function _winKey(base: string): string { return WIN ? `${base}:${WIN}` : base; }

// Base stacking level for every window. Giving each cell a z-index makes it its
// own stacking context, so the resize-handle overlays (z-index:9999) stay scoped
// to their window instead of floating above all of them.
const BASE_Z = 10;

export function isDesktopActive(): boolean {
  try {
    if (typeof localStorage === 'undefined') return false;
    // Per-monitor: each window's mode is independent (legacy fallback so an
    // existing global choice still applies on first run).
    return (localStorage.getItem(_winKey(LS_MODE))
      ?? localStorage.getItem(LS_MODE)) === '1';
  } catch {
    return false;
  }
}

/** Turn desktop (free-window) mode on/off for THIS monitor. The caller must
 *  re-apply the layout. */
export function setDesktopMode(on: boolean): void {
  try {
    if (typeof localStorage === 'undefined') return;
    if (on) localStorage.setItem(_winKey(LS_MODE), '1');
    else localStorage.removeItem(_winKey(LS_MODE));
  } catch {}
}

// ─── Lock ─────────────────────────────────────────────────────────────────────

let _locked = _loadLocked();

function _loadLocked(): boolean {
  try {
    if (typeof localStorage === 'undefined') return false;
    return (localStorage.getItem(_winKey(LS_LOCKED))
      ?? localStorage.getItem(LS_LOCKED)) === '1';
  } catch {
    return false;
  }
}

export function isDesktopLocked(): boolean {
  return _locked;
}

export function lockDesktop(): void {
  _locked = true;
  try { if (typeof localStorage !== 'undefined') localStorage.setItem(_winKey(LS_LOCKED), '1'); } catch {}
}

export function unlockDesktop(): void {
  _locked = false;
  try { if (typeof localStorage !== 'undefined') localStorage.removeItem(_winKey(LS_LOCKED)); } catch {}
}

// ─── Rects ────────────────────────────────────────────────────────────────────

type Rect = InstanceGeometry; // {x, y, w, h}
const _rects = new Map<string, Rect>();

function _loadRects(): void {
  try {
    if (typeof localStorage === 'undefined') return;
    const raw = localStorage.getItem(_winKey(LS_RECTS)) ?? localStorage.getItem(LS_RECTS);
    if (!raw) return;
    const obj = JSON.parse(raw) as Record<string, Rect>;
    for (const [id, r] of Object.entries(obj)) {
      if (r && typeof r.x === 'number') _rects.set(id, r);
    }
  } catch {}
}

export function saveDesktopRects(): void {
  try {
    if (typeof localStorage === 'undefined') return;
    const obj: Record<string, Rect> = {};
    for (const [id, r] of _rects) obj[id] = { ...r };
    localStorage.setItem(_winKey(LS_RECTS), JSON.stringify(obj));
  } catch {}
}

function _commitRect(id: string, r: Rect): void {
  _rects.set(id, { ...r });
  saveDesktopRects();
}

// ─── Z-order (bring-to-front) ───────────────────────────────────────────────────

const _z = new Map<string, number>();
let _topZ = BASE_Z;

function _loadZ(): void {
  try {
    if (typeof localStorage === 'undefined') return;
    const raw = localStorage.getItem(_winKey(LS_Z)) ?? localStorage.getItem(LS_Z);
    if (!raw) return;
    const obj = JSON.parse(raw) as Record<string, number>;
    for (const [id, z] of Object.entries(obj)) {
      if (typeof z === 'number') { _z.set(id, z); _topZ = Math.max(_topZ, z); }
    }
  } catch {}
}

function _saveZ(): void {
  try {
    if (typeof localStorage === 'undefined') return;
    const obj: Record<string, number> = {};
    for (const [id, z] of _z) obj[id] = z;
    localStorage.setItem(_winKey(LS_Z), JSON.stringify(obj));
  } catch {}
}

/** Raise a desktop window above all others (persisted). No-op outside desktop mode. */
export function bringPanelToFront(id: string): void {
  _topZ += 1;
  _z.set(id, _topZ);
  const el = _cellEls.get(id);
  if (el) el.style.zIndex = String(_topZ);
  _saveZ();
}

// ─── Default geometry ─────────────────────────────────────────────────────────

const MIN_W = 240;
const MIN_H = 160;
const TOPBAR_H = 56;
const GAP = 10;

/**
 * Tile panels into a simple grid based on their index.
 * Falls back gracefully when viewport is unknown.
 */
function _defaultRect(idx: number, total: number, vw: number, vh: number): Rect {
  const usableH = vh - TOPBAR_H;
  const cols = Math.max(1, Math.ceil(Math.sqrt(total)));
  const rows = Math.max(1, Math.ceil(total / cols));
  const cellW = Math.floor((vw - GAP * (cols + 1)) / cols);
  const cellH = Math.floor((usableH - GAP * (rows + 1)) / rows);
  const col = idx % cols;
  const row = Math.floor(idx / cols);
  return {
    x: GAP + col * (cellW + GAP),
    y: TOPBAR_H + GAP + row * (cellH + GAP),
    w: Math.max(MIN_W, cellW),
    h: Math.max(MIN_H, cellH),
  };
}

// ─── Cell map ─────────────────────────────────────────────────────────────────

let _container: HTMLElement | null = null;
const _cellEls = new Map<string, HTMLElement>(); // panelId → DOM element

export function getDesktopCellEl(id: string): HTMLElement | null {
  return _cellEls.get(id) ?? null;
}

// ─── Drag state ───────────────────────────────────────────────────────────────

interface DragState {
  panelId: string;
  zone: HandleZone;
  startX: number;
  startY: number;
  startGeom: Rect;
}

let _drag: DragState | null = null;

// ─── Init ─────────────────────────────────────────────────────────────────────

export function initDesktopLayout(container: HTMLElement): void {
  _container = container;
  _loadRects();
  _loadZ();
  _attachPointerListeners(container);
}

// ─── Apply ────────────────────────────────────────────────────────────────────

export function applyDesktopLayout(
  plugins: PanelPlugin[],
  state: SystemState,
  layoutId: string,
): void {
  if (!_container) return;

  // Switch container to absolute positioning
  _container.style.position = 'relative';
  _container.style.width = '100%';
  _container.style.height = '100%';
  _container.style.overflow = 'hidden';
  // Clear grid template (desktop mode doesn't use CSS grid)
  _container.style.display = 'block';
  _container.style.gridTemplateColumns = '';
  _container.style.gridTemplateRows = '';
  _container.style.gridTemplateAreas = '';

  const vw = _container.offsetWidth || window.innerWidth;
  const vh = _container.offsetHeight || window.innerHeight;

  // Initialise per-layout module sets from global state (once)
  initLayoutModulesFromGlobal(layoutId, plugins.map((p) => p.id));

  // Determine which panels are visible in this layout
  const visiblePlugins = plugins.filter((p) => {
    if (!isModuleEnabledForLayout(layoutId, p.id)) return false;
    if (!isPanelEnabled(p.id)) return false;
    return isPanelVisible(p, state);
  });

  // Hide panels that should not be visible
  for (const [id, el] of _cellEls) {
    const plugin = plugins.find((p) => p.id === id);
    const shouldShow = visiblePlugins.some((p) => p.id === id);
    if (!shouldShow && el.style.display !== 'none') {
      el.style.display = 'none';
      plugin?.suspend?.();
    }
  }

  visiblePlugins.forEach((plugin, idx) => {
    const wasHidden = !_cellEls.has(plugin.id) || _cellEls.get(plugin.id)!.style.display === 'none';

    const el = _ensureCell(plugin, state);

    // Assign default rect if none stored
    if (!_rects.has(plugin.id)) {
      const r = _defaultRect(idx, visiblePlugins.length, vw, vh);
      _rects.set(plugin.id, r);
    }

    const rect = _rects.get(plugin.id)!;
    _positionCell(el, rect);

    if (el.style.display === 'none') {
      el.style.display = '';
      if (wasHidden) plugin.resume?.();
    }
  });

  // Notify panels of their real pixel size next frame
  requestAnimationFrame(() => {
    for (const plugin of visiblePlugins) {
      const el = _cellEls.get(plugin.id);
      if (!el || el.style.display === 'none') continue;
      const r = el.getBoundingClientRect();
      try { plugin.onResize?.({ x: r.left, y: r.top, w: r.width, h: r.height }); } catch {}
    }
  });
}

// ─── Cell builder ─────────────────────────────────────────────────────────────

function _ensureCell(plugin: PanelPlugin, state: SystemState): HTMLElement {
  const existing = _cellEls.get(plugin.id);
  if (existing) return existing;
  if (!_container) throw new Error('[desktop] container not initialised');

  const el = document.createElement('div');
  el.className = 'dashboard-cell desktop-win';
  el.id = `cell-${plugin.id}`;
  el.setAttribute('data-plugin-id', plugin.id);
  el.style.cssText = [
    'position: absolute',
    'box-sizing: border-box',
    'overflow: hidden',
    'touch-action: none',
    'user-select: none',
    '-webkit-user-select: none',
  ].join('; ');

  // Apply the persisted stacking level (BASE_Z if never raised). Giving every
  // cell a z-index makes it its own stacking context so its resize-handle
  // overlays don't float above other windows.
  el.style.zIndex = String(_z.get(plugin.id) ?? BASE_Z);

  _container.appendChild(el);
  _cellEls.set(plugin.id, el);
  registerCellEl(plugin.id, el); // share with apply.ts getCellEl

  try {
    plugin.mount(el);
    plugin.resume?.();
    const budget = {
      graphFps: 30, graphMaxNodes: 300,
      chartFps: 30, chartMaxPoints: 500,
      animate: true, ws: 'all' as const,
      scoutMs: 3000, boardMs: 10000,
    };
    try { plugin.update(state, budget); } catch {}
  } catch (err) {
    console.error(`[desktop] ${plugin.id}.mount threw:`, err);
    el.innerHTML =
      `<div class="panel-header"><span class="panel-label">${plugin.id}</span></div>` +
      `<p class="offline-state">panel error — see console</p>`;
  }

  // Re-assert position:absolute after mount() — some plugins (e.g. tokens-day)
  // set el.style.position = 'relative' inside their mount() call.
  el.style.position = 'absolute';

  // Add transparent edge/corner handle overlays so pointer events reach
  // _onPointerDown even when the panel content includes an iframe.
  // These thin strips sit above the panel content (z-index: 9999) and
  // cover exactly the HANDLE_INSET region at each edge.
  _addResizeHandleOverlay(el);

  return el;
}

function _positionCell(el: HTMLElement, r: Rect): void {
  el.style.left   = `${r.x}px`;
  el.style.top    = `${r.y}px`;
  el.style.width  = `${r.w}px`;
  el.style.height = `${r.h}px`;
}

/**
 * Add 8 transparent edge/corner overlay strips to a desktop window so that
 * pointer events reach _onPointerDown even when the panel contains an iframe
 * (iframes absorb pointer events within their content area, preventing the
 * container's listener from firing).
 *
 * The strips cover exactly HANDLE_INSET px at each edge and have z-index:9999
 * so they sit above the panel content. They are pointer-events:all and fire
 * events that bubble up to the container.
 */
const OVERLAY_INSET = 16; // must match HANDLE_INSET in freeform.ts (fat = easy to grab)

function _addResizeHandleOverlay(cell: HTMLElement): void {
  // One transparent overlay strip per edge spanning the full edge length.
  // Corners are handled by the two overlapping edge strips.
  // These sit above the panel content (z-index: 9999) so they intercept
  // pointer events before iframes or other content can absorb them.
  const strips: Array<{ cssText: string }> = [
    // S: full-width strip at bottom
    { cssText: `position:absolute;top:auto;left:0;right:0;bottom:0;height:${OVERLAY_INSET}px;cursor:ns-resize;z-index:9999;pointer-events:all;` },
    // N: full-width strip at top
    { cssText: `position:absolute;top:0;left:0;right:0;bottom:auto;height:${OVERLAY_INSET}px;cursor:ns-resize;z-index:9999;pointer-events:all;` },
    // E: full-height strip at right
    { cssText: `position:absolute;top:0;left:auto;right:0;bottom:0;width:${OVERLAY_INSET}px;cursor:ew-resize;z-index:9999;pointer-events:all;` },
    // W: full-height strip at left
    { cssText: `position:absolute;top:0;left:0;right:auto;bottom:0;width:${OVERLAY_INSET}px;cursor:ew-resize;z-index:9999;pointer-events:all;` },
  ];

  for (const s of strips) {
    const handle = document.createElement('div');
    handle.className = 'desktop-resize-handle';
    handle.style.cssText = s.cssText;
    // Mark so event handler can detect it's a handle overlay
    handle.dataset['desktopHandle'] = '1';
    cell.appendChild(handle);
  }
}

// ─── Pointer events ───────────────────────────────────────────────────────────

function _attachPointerListeners(container: HTMLElement): void {
  // pointermove/up on container bubble phase is sufficient — pointer capture
  // redirects these to the capturing element (the cell), which still bubbles up.
  container.addEventListener('pointermove', _onPointerMove);
  container.addEventListener('pointerup',   _onPointerUp);
  container.addEventListener('pointercancel', _onPointerUp);
  // pointerdown: use capture phase so panel content calling stopPropagation
  // doesn't block edge/corner resize hit-testing on the container.
  container.addEventListener('pointerdown', _onPointerDown, { capture: true });
}

function _onPointerDown(e: PointerEvent): void {
  if (!isDesktopActive()) return;
  if (e.button !== 0) return;

  const cell = (e.target as HTMLElement).closest<HTMLElement>('.desktop-win');
  if (!cell) return;

  const panelId = cell.dataset['pluginId'];
  if (!panelId) return;

  // Clicking a window raises it to the front (even when locked — raising is not
  // moving). This is the natural "bring to front" alongside the Modules-menu button.
  bringPanelToFront(panelId);

  if (_locked) return;

  const rect = _rects.get(panelId);
  if (!rect) return;

  const cellRect = cell.getBoundingClientRect();
  const px = e.clientX - cellRect.left;
  const py = e.clientY - cellRect.top;
  const zone = hitTestHandle(px, py, cellRect.width, cellRect.height);
  if (zone === 'none') return;

  // 'body' zone: drag the window from ANYWHERE on it EXCEPT over interactive
  // content (buttons/inputs, charts, the embedded board iframe, editables). This
  // makes the whole panel an easy grab target instead of just the thin header.
  if (zone === 'body') {
    if ((e.target as HTMLElement).closest(
      'button, a, input, select, textarea, iframe, canvas, svg, [contenteditable], .no-drag',
    )) return;
  }

  e.preventDefault();
  cell.setPointerCapture(e.pointerId);

  _drag = {
    panelId,
    zone,
    startX: e.clientX,
    startY: e.clientY,
    startGeom: { ...rect },
  };
}

function _onPointerMove(e: PointerEvent): void {
  if (!_drag || !_container) return;

  const dx = e.clientX - _drag.startX;
  const dy = e.clientY - _drag.startY;
  const newGeom = applyDelta(_drag.startGeom, _drag.zone, dx, dy, DEFAULT_GRID_SIZE);
  const clamped = clampToViewport(newGeom, _container.offsetWidth, _container.offsetHeight);

  const el = _cellEls.get(_drag.panelId);
  if (el) {
    _positionCell(el, clamped);
    el.style.cursor = cursorForZone(_drag.zone);
  }
}

function _onPointerUp(e: PointerEvent): void {
  if (!_drag || !_container) return;

  const dx = e.type === 'pointercancel' ? 0 : e.clientX - _drag.startX;
  const dy = e.type === 'pointercancel' ? 0 : e.clientY - _drag.startY;
  const newGeom = applyDelta(_drag.startGeom, _drag.zone, dx, dy, DEFAULT_GRID_SIZE);
  const clamped = clampToViewport(newGeom, _container.offsetWidth, _container.offsetHeight);

  _commitRect(_drag.panelId, clamped);

  const el = _cellEls.get(_drag.panelId);
  if (el) {
    _positionCell(el, clamped); // ensure DOM reflects final geometry even if pointermove was missed
    el.style.cursor = '';
  }

  _drag = null;
}

// ─── Test helpers ─────────────────────────────────────────────────────────────

export function _clearDesktopForTest(): void {
  _container = null;
  _cellEls.clear();
  _rects.clear();
  _drag = null;
}
