/**
 * layout/presets.ts — Named layout presets for P1 free-form layout.
 *
 * A preset captures:
 *   - A snapshot of all module-instance geometries at save time.
 *   - An optional name chosen by the user.
 *
 * Persistence:
 *   Two localStorage keys mirror the `dash:disabledPanels` pattern:
 *     `dash:layoutPresets`  — JSON array of LayoutPreset objects.
 *     `dash:activePreset`   — string preset id OR 'template' (the default).
 *
 * Back-compat guarantee:
 *   When `dash:activePreset` is absent OR equals 'template', the template-
 *   based layout runs exactly as before — no instances, no free-form.
 *   Free-form ONLY activates when a non-template preset is the active one.
 *
 * Pure module — no DOM. Unit-testable.
 */

import type { InstanceGeometry } from '../plugins/instances.js';

// ─── Types ─────────────────────────────────────────────────────────────────────

/** Snapshot of one instance's geometry at preset save time. */
export interface PresetGeometryEntry {
  instanceId: string;
  geometry: InstanceGeometry;
}

/** A named layout preset. */
export interface LayoutPreset {
  /** Unique identifier (auto-generated). */
  readonly id: string;
  /** User-supplied name. */
  name: string;
  /** Geometry for every instance that was positioned when the preset was saved. */
  geometries: PresetGeometryEntry[];
  /** ISO timestamp of when the preset was last saved. */
  savedAt: string;
}

// ─── Constants ─────────────────────────────────────────────────────────────────

export const TEMPLATE_PRESET_ID = 'template';

const LS_PRESETS      = 'dash:layoutPresets';
const LS_ACTIVE       = 'dash:activePreset';

// ─── Storage helpers ───────────────────────────────────────────────────────────

function _loadPresets(): LayoutPreset[] {
  try {
    if (typeof localStorage === 'undefined') return [];
    const raw = localStorage.getItem(LS_PRESETS);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return (parsed as LayoutPreset[]).filter(
      (x) =>
        x != null &&
        typeof x === 'object' &&
        typeof x.id === 'string' &&
        typeof x.name === 'string' &&
        Array.isArray(x.geometries),
    );
  } catch {
    return [];
  }
}

function _savePresets(presets: LayoutPreset[]): void {
  try {
    if (typeof localStorage === 'undefined') return;
    localStorage.setItem(LS_PRESETS, JSON.stringify(presets));
  } catch {
    // Quota / privacy-mode — best effort.
  }
}

function _loadActiveId(): string {
  try {
    if (typeof localStorage === 'undefined') return TEMPLATE_PRESET_ID;
    return localStorage.getItem(LS_ACTIVE) ?? TEMPLATE_PRESET_ID;
  } catch {
    return TEMPLATE_PRESET_ID;
  }
}

function _saveActiveId(id: string): void {
  try {
    if (typeof localStorage === 'undefined') return;
    localStorage.setItem(LS_ACTIVE, id);
  } catch {
    // Best effort.
  }
}

// ─── ID generation ─────────────────────────────────────────────────────────────

function _generateId(): string {
  return `preset-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

// ─── In-memory state ───────────────────────────────────────────────────────────

let _presets: LayoutPreset[]   = _loadPresets();
let _activeId: string          = _loadActiveId();

// ─── Read API ──────────────────────────────────────────────────────────────────

/** All saved presets in insertion order (does not include the implicit 'template' preset). */
export function allPresets(): LayoutPreset[] {
  return _presets.slice();
}

/** Get a preset by id, or undefined. */
export function getPreset(id: string): LayoutPreset | undefined {
  return _presets.find((p) => p.id === id);
}

/**
 * The currently active preset id.
 * Returns `TEMPLATE_PRESET_ID` ('template') when no free-form preset is active.
 */
export function activePresetId(): string {
  return _activeId;
}

/**
 * Whether the dashboard should render in free-form mode right now.
 * Returns true only when an actual named preset (not 'template') is active AND
 * that preset exists in the store.
 */
export function isFreeformActive(): boolean {
  if (_activeId === TEMPLATE_PRESET_ID) return false;
  return _presets.some((p) => p.id === _activeId);
}

/**
 * Get the geometry entries for the active preset.
 * Returns an empty array when template mode is active.
 */
export function activePresetGeometries(): PresetGeometryEntry[] {
  if (!isFreeformActive()) return [];
  const preset = getPreset(_activeId);
  return preset ? preset.geometries.slice() : [];
}

// ─── Write API ─────────────────────────────────────────────────────────────────

/**
 * Save a new named preset (or overwrite an existing one by id).
 * `geometries` should come from the current instance store at save time.
 * Returns the saved preset.
 */
export function savePreset(
  name: string,
  geometries: PresetGeometryEntry[],
  existingId?: string,
): LayoutPreset {
  const id = existingId ?? _generateId();
  const preset: LayoutPreset = {
    id,
    name,
    geometries: geometries.map((g) => ({ ...g, geometry: { ...g.geometry } })),
    savedAt: new Date().toISOString(),
  };

  const idx = _presets.findIndex((p) => p.id === id);
  if (idx >= 0) {
    // Overwrite in-place (immutable update).
    _presets = [
      ..._presets.slice(0, idx),
      preset,
      ..._presets.slice(idx + 1),
    ];
  } else {
    _presets = [..._presets, preset];
  }

  _savePresets(_presets);
  return preset;
}

/**
 * Rename a preset.
 * Returns the updated preset, or undefined if not found.
 */
export function renamePreset(id: string, name: string): LayoutPreset | undefined {
  const idx = _presets.findIndex((p) => p.id === id);
  if (idx < 0) return undefined;
  const updated: LayoutPreset = { ..._presets[idx]!, name };
  _presets = [
    ..._presets.slice(0, idx),
    updated,
    ..._presets.slice(idx + 1),
  ];
  _savePresets(_presets);
  return updated;
}

/**
 * Delete a preset by id.
 * If the deleted preset was the active one, falls back to 'template'.
 * Returns true if removed, false if not found.
 */
export function deletePreset(id: string): boolean {
  const before = _presets.length;
  _presets = _presets.filter((p) => p.id !== id);
  if (_presets.length === before) return false;
  if (_activeId === id) {
    _activeId = TEMPLATE_PRESET_ID;
    _saveActiveId(_activeId);
  }
  _savePresets(_presets);
  return true;
}

/**
 * Switch to a preset by id.
 * Pass `TEMPLATE_PRESET_ID` to return to the template-based layout.
 * Returns false if the preset does not exist (and the switch does not happen).
 */
export function switchPreset(id: string): boolean {
  if (id === TEMPLATE_PRESET_ID) {
    _activeId = TEMPLATE_PRESET_ID;
    _saveActiveId(_activeId);
    return true;
  }
  if (!_presets.some((p) => p.id === id)) return false;
  _activeId = id;
  _saveActiveId(_activeId);
  return true;
}

/**
 * Update the geometry entries of the current active preset in-place.
 * Used by the drag/resize engine to persist geometry changes immediately.
 * No-op when template mode is active.
 * Returns the updated preset, or undefined when template is active.
 */
export function updateActivePresetGeometries(
  geometries: PresetGeometryEntry[],
): LayoutPreset | undefined {
  if (!isFreeformActive()) return undefined;
  return savePreset(
    getPreset(_activeId)?.name ?? 'Untitled',
    geometries,
    _activeId,
  );
}

// ─── Test helpers ──────────────────────────────────────────────────────────────

/** Reset all preset state (for unit tests only). */
export function _clearPresetsForTest(): void {
  _presets = [];
  _activeId = TEMPLATE_PRESET_ID;
}
