/**
 * plugins/registry.ts — Plugin self-registration registry.
 *
 * Plugins import this module and call register({...}) as a side-effect on load.
 * main.ts imports the index barrel (src/plugins/index.ts) to trigger all registrations,
 * then calls all() to get the list.
 *
 * Design: no circular dependencies — registry knows nothing about layout or state.
 */

import type { PanelPlugin } from './contract.js';

const _registry: Map<string, PanelPlugin> = new Map();

/**
 * Register a plugin. Called as a side-effect when a plugin module is imported.
 * Duplicate IDs are rejected with a warning.
 */
export function register(plugin: PanelPlugin): void {
  if (_registry.has(plugin.id)) {
    console.warn(`[registry] duplicate plugin id "${plugin.id}" — ignoring`);
    return;
  }
  _registry.set(plugin.id, plugin);
}

/** Return all registered plugins in registration order. */
export function all(): PanelPlugin[] {
  return Array.from(_registry.values());
}

// ─── Enable / disable (CC6 — persisted panel toggles) ─────────────────────────
//
// We persist the DISABLED set (not enabled) so a newly-added panel defaults ON.

const LS_DISABLED = 'dash:disabledPanels';
const _disabled = new Set<string>(_loadDisabled());

function _loadDisabled(): string[] {
  try {
    if (typeof localStorage === 'undefined') return [];
    const raw = localStorage.getItem(LS_DISABLED);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

function _persistDisabled(): void {
  try {
    if (typeof localStorage === 'undefined') return;
    localStorage.setItem(LS_DISABLED, JSON.stringify([..._disabled]));
  } catch {
    // ignore quota / privacy-mode errors
  }
}

export function isPanelEnabled(id: string): boolean {
  return !_disabled.has(id);
}

export function setPanelEnabled(id: string, on: boolean): void {
  if (on) _disabled.delete(id);
  else _disabled.add(id);
  _persistDisabled();
}

/** Registered plugins minus the user-disabled ones (drives the live layout). */
export function enabled(): PanelPlugin[] {
  return all().filter((p) => isPanelEnabled(p.id));
}

/** Get a specific plugin by id (or undefined). */
export function get(id: string): PanelPlugin | undefined {
  return _registry.get(id);
}

/** Clear registry (for unit tests only). */
export function _clearForTest(): void {
  _registry.clear();
}
