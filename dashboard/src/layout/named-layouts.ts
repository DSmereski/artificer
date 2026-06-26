/**
 * layout/named-layouts.ts — #211 save / restore named layout snapshots.
 *
 * "Save a customized layout": capture the current arrangement (desktop mode +
 * window rects + z-order + lock + template override) under a name, then restore
 * it later. Snapshots are per-monitor — keyed by ?win like the rest of the
 * layout system — so each screen has its own named layouts.
 *
 * A snapshot stores the *values* of the layout localStorage keys for THIS
 * window. Restoring writes them back (removing any the snapshot didn't have) and
 * the caller reloads so the layout engine re-initialises from a clean state —
 * the same approach the template selector already uses for mode switches.
 */

// The localStorage keys that together define a window's layout. Kept in sync
// with desktop.ts (mode/rects/locked/z) and template-select.ts (template).
const LAYOUT_KEYS = [
  'dash:desktopMode',
  'dash:desktopRects',
  'dash:desktopLocked',
  'dash:desktopZ',
  'dash:layoutTemplate',
] as const;

const LS_CATALOG = 'dash:savedLayouts';
const LS_SNAP_PREFIX = 'dash:savedLayout:';

const WIN: string = (() => {
  try { return new URLSearchParams(location.search).get('win') ?? ''; }
  catch { return ''; }
})();
function _winKey(base: string): string { return WIN ? `${base}:${WIN}` : base; }

type Snapshot = Record<string, string | null>;

function _readCatalog(): string[] {
  try {
    if (typeof localStorage === 'undefined') return [];
    const raw = localStorage.getItem(_winKey(LS_CATALOG));
    if (!raw) return [];
    const arr = JSON.parse(raw) as unknown;
    return Array.isArray(arr) ? arr.filter((x): x is string => typeof x === 'string') : [];
  } catch { return []; }
}

function _writeCatalog(names: string[]): void {
  try {
    if (typeof localStorage === 'undefined') return;
    localStorage.setItem(_winKey(LS_CATALOG), JSON.stringify(names));
  } catch {}
}

/** Names of saved layouts for this monitor, in insertion order. */
export function listLayouts(): string[] {
  return _readCatalog();
}

/** Snapshot the current layout under *name* (overwrites an existing one). */
export function saveCurrentLayout(name: string): void {
  const clean = name.trim().slice(0, 40);
  if (!clean || typeof localStorage === 'undefined') return;
  const snap: Snapshot = {};
  for (const base of LAYOUT_KEYS) {
    try { snap[base] = localStorage.getItem(_winKey(base)); } catch { snap[base] = null; }
  }
  try { localStorage.setItem(_winKey(LS_SNAP_PREFIX + clean), JSON.stringify(snap)); } catch { return; }
  const cat = _readCatalog();
  if (!cat.includes(clean)) { cat.push(clean); _writeCatalog(cat); }
}

/**
 * Restore the named layout's localStorage state for this monitor. Returns true
 * if a snapshot was found and written (the caller should reload to apply it).
 */
export function applyLayout(name: string): boolean {
  if (typeof localStorage === 'undefined') return false;
  let raw: string | null = null;
  try { raw = localStorage.getItem(_winKey(LS_SNAP_PREFIX + name)); } catch { return false; }
  if (!raw) return false;
  let snap: Snapshot;
  try { snap = JSON.parse(raw) as Snapshot; } catch { return false; }
  for (const base of LAYOUT_KEYS) {
    const v = snap[base];
    try {
      if (v === null || v === undefined) localStorage.removeItem(_winKey(base));
      else localStorage.setItem(_winKey(base), v);
    } catch {}
  }
  return true;
}

/** Forget a saved layout. */
export function deleteLayout(name: string): void {
  try { if (typeof localStorage !== 'undefined') localStorage.removeItem(_winKey(LS_SNAP_PREFIX + name)); } catch {}
  _writeCatalog(_readCatalog().filter((n) => n !== name));
}
