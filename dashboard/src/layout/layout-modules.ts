/**
 * layout/layout-modules.ts — Per-layout module enabled sets (DL3).
 *
 * Each layout id (e.g. 'ultrawide', 'wide', 'portrait', 'auto') has its own
 * set of disabled panels. Falls back to the global registry state on first use.
 * Only consulted when desktop mode is enabled (dash:desktopMode=1).
 */

import { isPanelEnabled as globalIsPanelEnabled } from '../plugins/registry.js';

const LS_PREFIX = 'dash:layoutModules:';

// Map of layoutId → Set of disabled panelIds
const _disabled = new Map<string, Set<string>>();

function _lsKey(layoutId: string): string {
  return `${LS_PREFIX}${layoutId}`;
}

function _load(layoutId: string): Set<string> {
  try {
    if (typeof localStorage === 'undefined') return new Set();
    const raw = localStorage.getItem(_lsKey(layoutId));
    if (!raw) return new Set();
    const arr = JSON.parse(raw) as unknown;
    return new Set(Array.isArray(arr) ? (arr as string[]) : []);
  } catch {
    return new Set();
  }
}

function _persist(layoutId: string, disabled: Set<string>): void {
  try {
    if (typeof localStorage === 'undefined') return;
    localStorage.setItem(_lsKey(layoutId), JSON.stringify([...disabled]));
  } catch {}
}

function _getOrInit(layoutId: string): Set<string> {
  if (!_disabled.has(layoutId)) {
    _disabled.set(layoutId, _load(layoutId));
  }
  return _disabled.get(layoutId)!;
}

/** Whether a panel is enabled for the given layout. Falls back to global state if layout not yet initialised. */
export function isModuleEnabledForLayout(layoutId: string, panelId: string): boolean {
  if (!_disabled.has(layoutId) && typeof localStorage !== 'undefined') {
    const raw = localStorage.getItem(_lsKey(layoutId));
    if (raw === null) return globalIsPanelEnabled(panelId);
  }
  return !_getOrInit(layoutId).has(panelId);
}

export function setModuleEnabledForLayout(layoutId: string, panelId: string, on: boolean): void {
  const set = _getOrInit(layoutId);
  const next = new Set(set);
  if (on) next.delete(panelId);
  else next.add(panelId);
  _disabled.set(layoutId, next);
  _persist(layoutId, next);
}

/**
 * Initialise a layout's module set from global state (no-op if already persisted).
 * Call when entering a layout for the first time.
 */
export function initLayoutModulesFromGlobal(layoutId: string, allPanelIds: string[]): void {
  if (_disabled.has(layoutId)) return;
  const raw = typeof localStorage !== 'undefined' ? localStorage.getItem(_lsKey(layoutId)) : null;
  if (raw !== null) return;
  const set = new Set<string>();
  for (const id of allPanelIds) {
    if (!globalIsPanelEnabled(id)) set.add(id);
  }
  _disabled.set(layoutId, set);
  _persist(layoutId, set);
}

/** Reset for unit tests. */
export function _clearLayoutModulesForTest(): void {
  _disabled.clear();
}
