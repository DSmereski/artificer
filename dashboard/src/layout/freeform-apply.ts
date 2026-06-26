/**
 * layout/freeform-apply.ts — DOM layer for free-form drag/resize (P1).
 *
 * This module owns the pointer-event listeners and CSS positioning for the
 * free-form layout mode. It is the ONLY place that touches the DOM in Spine B.
 *
 * Responsibilities:
 *   1. Position each instance panel as `position: absolute` using its stored
 *      geometry (x,y,w,h in px).
 *   2. Attach pointermove / pointerdown / pointerup to the container so dragging
 *      the panel body moves it and dragging an edge/corner resizes it.
 *   3. Write geometry back to the instance store + the active preset after each
 *      drag (so it persists across reload).
 *   4. Expose `initFreeformApplier`, `applyFreeformLayout`, and
 *      `teardownFreeformApplier` for main.ts to call.
 *
 * Mouse-only (pointer events work fine on mouse; keyboard is NOT required —
 * Lively only forwards mouse events).
 *
 * Back-compat:
 *   When template mode is active (`isFreeformActive() === false`) this module
 *   does nothing — all function calls are no-ops. The template-based applier
 *   in apply.ts continues to manage the DOM.
 */

import type { PanelPlugin } from '../plugins/contract.js';
import type { SystemState } from '../state/types.js';
import type { InstanceGeometry } from '../plugins/instances.js';
import {
  allInstances,
  updateInstanceGeometry,
  getInstance,
} from '../plugins/instances.js';
import {
  isFreeformActive,
  activePresetGeometries,
  updateActivePresetGeometries,
  type PresetGeometryEntry,
} from './presets.js';
import {
  hitTestHandle,
  cursorForZone,
  applyDelta,
  clampToViewport,
  defaultGeometry,
  DEFAULT_GRID_SIZE,
  type HandleZone,
} from './freeform.js';
import { get as getPlugin } from '../plugins/registry.js';

// ─── State ─────────────────────────────────────────────────────────────────────

let _container: HTMLElement | null = null;
const _cellEls = new Map<string, HTMLElement>(); // instanceId → DOM element

// ─── Drag state ────────────────────────────────────────────────────────────────

interface DragState {
  instanceId: string;
  zone: HandleZone;
  startX: number;
  startY: number;
  startGeom: InstanceGeometry;
}

let _drag: DragState | null = null;

// ─── Initialise ────────────────────────────────────────────────────────────────

export function initFreeformApplier(container: HTMLElement): void {
  _container = container;
  _attachContainerListeners(container);
}

// ─── Apply ─────────────────────────────────────────────────────────────────────

/**
 * Apply the free-form layout for the current set of instances.
 * Called by the main store subscription in place of `applyTemplate` when free-
 * form mode is active.
 *
 * For each instance:
 *   1. Ensure a DOM cell exists and the plugin is mounted.
 *   2. Position it using the instance's persisted geometry (falling back to a
 *      default geometry if none is set yet).
 *   3. Write the default geometry back to the store so it persists on first use.
 *
 * Instances that have been removed from the store are hidden.
 */
export function applyFreeformLayout(
  plugins: PanelPlugin[],
  state: SystemState,
): void {
  if (!_container) return;

  // Switch the container to absolute positioning (override the CSS grid).
  _container.style.cssText = [
    'position: relative',
    'width: 100%',
    'height: 100%',
    'overflow: hidden',
    'background: transparent',
  ].join('; ');

  const instances = allInstances();
  const activeIds = new Set(instances.map((i) => i.instanceId));

  // Hide cells for instances that no longer exist.
  for (const [instId, el] of _cellEls) {
    if (!activeIds.has(instId)) {
      el.style.display = 'none';
    }
  }

  for (const inst of instances) {
    const plugin = getPlugin(inst.type) ??
      plugins.find((p) => p.id === inst.type);
    if (!plugin) continue;

    // Resolve geometry: use stored or allocate a default.
    let geom = inst.geometry ?? _presetGeomFor(inst.instanceId);
    if (!geom) {
      geom = defaultGeometry(DEFAULT_GRID_SIZE);
      updateInstanceGeometry(inst.instanceId, geom);
      _syncPreset();
    }

    const el = _ensureCell(inst.instanceId, plugin, state);
    _positionCell(el, geom);
    el.style.display = '';
  }
}

/**
 * Tear down the free-form applier: remove pointer listeners, hide all cells.
 * Called when switching back to template mode.
 */
export function teardownFreeformApplier(): void {
  // The container listeners are attached on the container itself; we let them
  // remain (they are no-ops when `_drag` is null and free-form is inactive).
  // Just hide all instance cells so the template applier can take over cleanly.
  for (const el of _cellEls.values()) {
    el.style.display = 'none';
  }
}

// ─── Cell management ──────────────────────────────────────────────────────────

function _ensureCell(
  instanceId: string,
  plugin: PanelPlugin,
  state: SystemState,
): HTMLElement {
  const existing = _cellEls.get(instanceId);
  if (existing) return existing;

  if (!_container) throw new Error('[freeform-apply] container not initialised');

  const el = document.createElement('div');
  el.className = 'dashboard-cell freeform-cell';
  el.id = `cell-inst-${instanceId}`;
  el.setAttribute('data-instance-id', instanceId);
  el.setAttribute('data-plugin-id', plugin.id);

  el.style.cssText = [
    'position: absolute',
    'box-sizing: border-box',
    'overflow: hidden',
    'touch-action: none',
    'user-select: none',
    '-webkit-user-select: none',
  ].join('; ');

  _container.appendChild(el);
  _cellEls.set(instanceId, el);

  // Mount the plugin into the cell.
  try {
    plugin.mount(el);
    plugin.resume?.();
    // Fire initial update so the panel renders immediately.
    const budget = {
      graphFps: 30, graphMaxNodes: 300,
      chartFps: 30, chartMaxPoints: 500,
      animate: true, ws: 'all' as const,
      scoutMs: 3000, boardMs: 10000,
    };
    try { plugin.update(state, budget); } catch { /* non-fatal */ }
  } catch (err) {
    console.error(`[freeform] ${plugin.id}.mount threw:`, err);
    el.innerHTML =
      `<div class="panel-header"><span class="panel-label">${plugin.id}</span></div>` +
      `<p class="offline-state">panel error — see console</p>`;
  }

  return el;
}

function _positionCell(el: HTMLElement, geom: InstanceGeometry): void {
  el.style.left   = `${geom.x}px`;
  el.style.top    = `${geom.y}px`;
  el.style.width  = `${geom.w}px`;
  el.style.height = `${geom.h}px`;
}

// ─── Preset geometry lookup ───────────────────────────────────────────────────

/** Look up persisted geometry from the active preset for a given instanceId. */
function _presetGeomFor(instanceId: string): InstanceGeometry | null {
  const entries = activePresetGeometries();
  const entry = entries.find((e) => e.instanceId === instanceId);
  return entry ? { ...entry.geometry } : null;
}

/** Sync all current instance geometries back into the active preset. */
function _syncPreset(): void {
  const instances = allInstances();
  const geometries: PresetGeometryEntry[] = instances
    .filter((i) => i.geometry != null)
    .map((i) => ({
      instanceId: i.instanceId,
      geometry: { ...i.geometry! },
    }));
  updateActivePresetGeometries(geometries);
}

// ─── Pointer event handlers ───────────────────────────────────────────────────

function _attachContainerListeners(container: HTMLElement): void {
  container.addEventListener('pointerdown', _onPointerDown);
  container.addEventListener('pointermove', _onPointerMove);
  container.addEventListener('pointerup',   _onPointerUp);
  container.addEventListener('pointercancel', _onPointerUp);
}

function _onPointerDown(e: PointerEvent): void {
  // Only process when free-form is active.
  if (!isFreeformActive()) return;
  if (e.button !== 0) return; // left-click only

  const cell = (e.target as HTMLElement).closest<HTMLElement>('.freeform-cell');
  if (!cell) return;

  const instanceId = cell.dataset['instanceId'];
  if (!instanceId) return;

  const inst = getInstance(instanceId);
  if (!inst?.geometry) return;

  const cellRect = cell.getBoundingClientRect();
  const px = e.clientX - cellRect.left;
  const py = e.clientY - cellRect.top;
  const zone = hitTestHandle(px, py, cellRect.width, cellRect.height);

  if (zone === 'none') return;

  // Don't initiate drag from interactive controls inside the panel.
  if (
    zone === 'body' &&
    (e.target as HTMLElement).closest('button, input, select, a, textarea')
  ) return;

  e.preventDefault();
  cell.setPointerCapture(e.pointerId);

  _drag = {
    instanceId,
    zone,
    startX: e.clientX,
    startY: e.clientY,
    startGeom: { ...inst.geometry },
  };
}

function _onPointerMove(e: PointerEvent): void {
  if (!_drag || !_container) return;

  const dx = e.clientX - _drag.startX;
  const dy = e.clientY - _drag.startY;

  const newGeom = applyDelta(_drag.startGeom, _drag.zone, dx, dy, DEFAULT_GRID_SIZE);
  const clamped = clampToViewport(
    newGeom,
    _container.offsetWidth,
    _container.offsetHeight,
  );

  // Live-update DOM position for smooth dragging (no store write yet — avoids
  // triggering a full re-render on every mouse move).
  const el = _cellEls.get(_drag.instanceId);
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
  const clamped = clampToViewport(
    newGeom,
    _container.offsetWidth,
    _container.offsetHeight,
  );

  // Commit to store + preset (persists across reload).
  updateInstanceGeometry(_drag.instanceId, clamped);
  _syncPreset();

  // Reset cursor
  const el = _cellEls.get(_drag.instanceId);
  if (el) el.style.cursor = '';

  _drag = null;
}

// ─── Cursor hint on hover (outside of drag) ────────────────────────────────────

/**
 * Attach per-cell pointermove to update the cursor when the user hovers near
 * a resize handle. Called from applyFreeformLayout after a cell is created.
 *
 * We use document-level mousemove on cells rather than the container because
 * we only need cursor updates (not position changes) — much cheaper.
 */
export function attachCellCursorHint(el: HTMLElement): void {
  el.addEventListener('pointermove', (e: PointerEvent) => {
    if (_drag) return; // During a drag, cursor is managed by _onPointerMove.
    if (!isFreeformActive()) return;
    const rect = el.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const zone = hitTestHandle(px, py, rect.width, rect.height);
    el.style.cursor = cursorForZone(zone);
  });
  el.addEventListener('pointerleave', () => {
    if (!_drag) el.style.cursor = '';
  });
}

// ─── Test helper ──────────────────────────────────────────────────────────────

/** Reset internal DOM state for tests. */
export function _clearFreeformApplyForTest(): void {
  _container = null;
  _cellEls.clear();
  _drag = null;
}
