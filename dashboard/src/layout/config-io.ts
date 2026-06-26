/**
 * layout/config-io.ts — Workspace config export / import (v-Next P6).
 *
 * A localStorage JSON round-trip for the WHOLE workspace:
 *   - module instances   (localStorage `dash:moduleInstances`)
 *   - layout presets      (localStorage `dash:layoutPresets`)
 *   - active preset id     (localStorage `dash:activePreset`)
 *   - active board         (localStorage `dash:activeBoard`)
 *
 * `exportWorkspace()` gathers them into one JSON object and returns the string.
 * `importWorkspace(json)` parses + VALIDATES at the boundary (version + shape +
 * size) and writes the pieces back. It NEVER throws and writes NOTHING on any
 * validation failure — fail fast, no partial writes.
 *
 * No network, no file I/O — pure localStorage. The validation logic is pure +
 * unit-testable; localStorage access is isolated in tiny helpers.
 */

// ─── Constants ─────────────────────────────────────────────────────────────────

export const CONFIG_VERSION = 1;

/** localStorage keys the round-trip owns (must match the modules that write them). */
const LS_INSTANCES    = 'dash:moduleInstances';
const LS_PRESETS      = 'dash:layoutPresets';
const LS_ACTIVE_PRESET = 'dash:activePreset';
const LS_ACTIVE_BOARD  = 'dash:activeBoard';

/**
 * Hard ceiling on an imported payload (chars). Guards against a pasted blob
 * blowing out localStorage / the parser. 1 MiB is far larger than any real
 * workspace (instances + presets) yet small enough to reject abuse fast.
 */
const MAX_IMPORT_CHARS = 1_048_576;

// ─── Types ─────────────────────────────────────────────────────────────────────

export interface WorkspaceConfig {
  version: number;
  /** Raw module-instance array (as stored). */
  instances: unknown[];
  /** Raw layout-preset array (as stored). */
  presets: unknown[];
  /** Active preset id ('template' or a preset id), or null. */
  activePreset: string | null;
  /** Active board id, or null (= all boards). */
  activeBoard: string | null;
}

export interface ImportResult {
  ok: boolean;
  error?: string;
}

// ─── localStorage helpers (isolated so the validation stays pure) ──────────────

function _read(key: string): string | null {
  try {
    if (typeof localStorage === 'undefined') return null;
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function _write(key: string, value: string | null): void {
  try {
    if (typeof localStorage === 'undefined') return;
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // Quota / privacy-mode — best effort, same pattern as the other modules.
  }
}

/** Parse a stored JSON array, defaulting to [] on any problem. */
function _readArray(key: string): unknown[] {
  const raw = _read(key);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

// ─── Export ────────────────────────────────────────────────────────────────────

/**
 * Snapshot the full workspace to a JSON string.
 * Always returns valid JSON with `version` + the four sections.
 */
export function exportWorkspace(): string {
  const config: WorkspaceConfig = {
    version: CONFIG_VERSION,
    instances: _readArray(LS_INSTANCES),
    presets: _readArray(LS_PRESETS),
    activePreset: _read(LS_ACTIVE_PRESET),
    activeBoard: _read(LS_ACTIVE_BOARD),
  };
  return JSON.stringify(config);
}

// ─── Validation (pure — exported for tests) ────────────────────────────────────

/**
 * Validate a parsed value as a WorkspaceConfig.
 * Returns the typed config on success, or an error string on failure.
 * Pure: does not touch localStorage.
 */
export function validateConfig(value: unknown): WorkspaceConfig | string {
  if (value === null || typeof value !== 'object') return 'not an object';
  const obj = value as Record<string, unknown>;

  if (obj['version'] !== CONFIG_VERSION) {
    return `unsupported version (expected ${CONFIG_VERSION})`;
  }
  if (!Array.isArray(obj['instances'])) return 'instances must be an array';
  if (!Array.isArray(obj['presets'])) return 'presets must be an array';

  const activePreset = obj['activePreset'];
  if (activePreset !== null && typeof activePreset !== 'string') {
    return 'activePreset must be a string or null';
  }
  const activeBoard = obj['activeBoard'];
  if (activeBoard !== null && typeof activeBoard !== 'string') {
    return 'activeBoard must be a string or null';
  }

  return {
    version: CONFIG_VERSION,
    instances: obj['instances'],
    presets: obj['presets'],
    activePreset: activePreset as string | null,
    activeBoard: activeBoard as string | null,
  };
}

// ─── Import ────────────────────────────────────────────────────────────────────

/**
 * Parse + validate a workspace JSON string and write it back to localStorage.
 * Never throws. Writes NOTHING unless the whole payload validates.
 *
 * @returns `{ok:true}` on success, `{ok:false, error}` on any failure.
 */
export function importWorkspace(json: string): ImportResult {
  if (typeof json !== 'string' || json.length === 0) {
    return { ok: false, error: 'empty input' };
  }
  if (json.length > MAX_IMPORT_CHARS) {
    return { ok: false, error: 'input too large' };
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(json);
  } catch {
    return { ok: false, error: 'invalid JSON' };
  }

  const result = validateConfig(parsed);
  if (typeof result === 'string') {
    return { ok: false, error: result };
  }

  // All-or-nothing write — validation passed, so commit every section.
  _write(LS_INSTANCES, JSON.stringify(result.instances));
  _write(LS_PRESETS, JSON.stringify(result.presets));
  _write(LS_ACTIVE_PRESET, result.activePreset);
  _write(LS_ACTIVE_BOARD, result.activeBoard);

  return { ok: true };
}
