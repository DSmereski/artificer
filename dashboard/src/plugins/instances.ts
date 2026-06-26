/**
 * plugins/instances.ts — Module-instance model layer (P0, v-Next Spine A).
 *
 * An INSTANCE is `{ instanceId, type, settings, geometry }`.
 * Multiple instances of the same plugin type can coexist.
 *
 * Back-compat guarantee:
 *   When no instances are stored in localStorage the instance layer is
 *   dormant — `hasInstances()` returns false and the existing single-instance-
 *   per-plugin path in registry.ts / main.ts continues to operate unchanged.
 *   The live wallpaper never breaks mid-build.
 *
 * Persistence:
 *   Mirrors the `dash:disabledPanels` pattern in registry.ts — a single JSON
 *   blob in localStorage under the key `dash:moduleInstances`.
 *
 * Geometry:
 *   Stored here but intentionally unused until P1 (free-form drag/resize).
 *   Defaults to null; P1 will populate and consume it.
 */

// ─── Types ────────────────────────────────────────────────────────────────────

/** Persisted geometry (null until P1 free-form layout is active). */
export interface InstanceGeometry {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** A concrete instance of a plugin type with its own settings and geometry. */
export interface ModuleInstance {
  /** Globally unique instance identifier (UUID-ish, generated at creation time). */
  readonly instanceId: string;
  /** The plugin type id this instance maps to (matches `PanelPlugin.id`). */
  readonly type: string;
  /** Per-instance settings bag. Merged with the plugin's `defaultSettings`. */
  settings: Record<string, unknown>;
  /** Free-form position/size — null until P1 layout engine is active. */
  geometry: InstanceGeometry | null;
}

// ─── Storage ──────────────────────────────────────────────────────────────────

const LS_INSTANCES = 'dash:moduleInstances';

/** Generate a simple unique id (no external deps). */
function _generateId(): string {
  return `inst-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function _load(): ModuleInstance[] {
  try {
    if (typeof localStorage === 'undefined') return [];
    const raw = localStorage.getItem(LS_INSTANCES);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    // Validate shape: each entry must have instanceId + type strings
    return (parsed as ModuleInstance[]).filter(
      (x) =>
        x != null &&
        typeof x === 'object' &&
        typeof x.instanceId === 'string' &&
        typeof x.type === 'string',
    );
  } catch {
    return [];
  }
}

function _persist(instances: ModuleInstance[]): void {
  try {
    if (typeof localStorage === 'undefined') return;
    localStorage.setItem(LS_INSTANCES, JSON.stringify(instances));
  } catch {
    // Ignore quota / privacy-mode errors — same pattern as registry.ts
  }
}

// ─── In-memory state ──────────────────────────────────────────────────────────

let _instances: ModuleInstance[] = _load();

// ─── Read API ─────────────────────────────────────────────────────────────────

/**
 * Whether the instance layer is active.
 * False when no instances have been added yet (back-compat path stays in effect).
 */
export function hasInstances(): boolean {
  return _instances.length > 0;
}

/**
 * Resolve the effective settings for a panel cell, merged over the plugin's
 * defaults. Used by single-`_rootEl` panels that want their settings to take
 * effect in BOTH layout paths:
 *
 *   1. Free-form path  — the cell carries a `data-instance-id`; we read that
 *      instance's settings (the weather.ts / tokens-day.ts convention).
 *   2. Template path    — the cell has NO `data-instance-id`. We fall back to
 *      the FIRST instance of this plugin type (if the user created one via the
 *      Module Manager), else to the plugin defaults.
 *
 * Returns `{ ...defaults, ...instanceSettings }`. With no instance at all this
 * is exactly `defaults`, so a fresh user gets today's behavior (back-compat).
 *
 * @param el       The cell element (may be null for module-level callers).
 * @param type     The plugin type id (matches `PanelPlugin.id`).
 * @param defaults The plugin's `defaultSettings` (the back-compat baseline).
 */
export function resolveSettings<T extends object>(
  el: HTMLElement | null,
  type: string,
  defaults: T,
): T {
  let raw: Record<string, unknown> | undefined;

  const instanceId = el?.dataset['instanceId'] ?? null;
  if (instanceId) {
    raw = getInstance(instanceId)?.settings;
  }
  if (!raw) {
    // Template path (or no data-instance-id): adopt the first instance of this
    // type's settings if one exists; otherwise pure defaults.
    raw = _instances.find((i) => i.type === type)?.settings;
  }

  return raw ? { ...defaults, ...raw } : { ...defaults };
}

/** Return all instances in insertion order. */
export function allInstances(): ModuleInstance[] {
  return _instances.slice();
}

/** Return all instances of a given plugin type. */
export function instancesOfType(type: string): ModuleInstance[] {
  return _instances.filter((i) => i.type === type);
}

/** Get a single instance by id (or undefined). */
export function getInstance(instanceId: string): ModuleInstance | undefined {
  return _instances.find((i) => i.instanceId === instanceId);
}

// ─── Write API ────────────────────────────────────────────────────────────────

/**
 * Add a new instance of a plugin type.
 * `initialSettings` is merged with defaults; geometry defaults to null.
 * Returns the new instance.
 */
export function addInstance(
  type: string,
  initialSettings: Record<string, unknown> = {},
): ModuleInstance {
  const instance: ModuleInstance = {
    instanceId: _generateId(),
    type,
    settings: { ...initialSettings },
    geometry: null,
  };
  _instances = [..._instances, instance];
  _persist(_instances);
  return instance;
}

/**
 * Duplicate an existing instance, producing a second copy with a new id.
 * The duplicate starts with the same settings and geometry as the source.
 * Returns the new instance, or undefined if the source does not exist.
 */
export function duplicateInstance(instanceId: string): ModuleInstance | undefined {
  const src = getInstance(instanceId);
  if (!src) return undefined;
  const copy: ModuleInstance = {
    instanceId: _generateId(),
    type: src.type,
    settings: { ...src.settings },
    geometry: src.geometry ? { ...src.geometry } : null,
  };
  _instances = [..._instances, copy];
  _persist(_instances);
  return copy;
}

/**
 * Remove an instance by id.
 * Returns true if removed, false if not found.
 */
export function removeInstance(instanceId: string): boolean {
  const before = _instances.length;
  _instances = _instances.filter((i) => i.instanceId !== instanceId);
  if (_instances.length === before) return false;
  _persist(_instances);
  return true;
}

/**
 * Update per-instance settings (partial merge).
 * Returns the updated instance, or undefined if not found.
 */
export function updateInstanceSettings(
  instanceId: string,
  patch: Record<string, unknown>,
): ModuleInstance | undefined {
  const idx = _instances.findIndex((i) => i.instanceId === instanceId);
  if (idx === -1) return undefined;
  const updated: ModuleInstance = {
    ..._instances[idx]!,
    settings: { ..._instances[idx]!.settings, ...patch },
  };
  _instances = [
    ..._instances.slice(0, idx),
    updated,
    ..._instances.slice(idx + 1),
  ];
  _persist(_instances);
  return updated;
}

/**
 * Update the geometry of an instance (written by P1 layout engine).
 * Returns the updated instance, or undefined if not found.
 */
export function updateInstanceGeometry(
  instanceId: string,
  geometry: InstanceGeometry,
): ModuleInstance | undefined {
  const idx = _instances.findIndex((i) => i.instanceId === instanceId);
  if (idx === -1) return undefined;
  const updated: ModuleInstance = {
    ..._instances[idx]!,
    geometry: { ...geometry },
  };
  _instances = [
    ..._instances.slice(0, idx),
    updated,
    ..._instances.slice(idx + 1),
  ];
  _persist(_instances);
  return updated;
}

// ─── Test helpers ─────────────────────────────────────────────────────────────

/** Reset all instances (for unit tests only). */
export function _clearInstancesForTest(): void {
  _instances = [];
}
