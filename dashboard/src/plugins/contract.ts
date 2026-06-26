/**
 * plugins/contract.ts — PanelPlugin interface contract (frozen in Pv2.1).
 *
 * Adding a panel = dropping a file that calls register({...}).
 * No edits to core files required when adding new panels.
 *
 * Version: 2 (bump on breaking changes; plugins should tolerate unknown budget fields).
 *
 * P0 v-Next additions (all optional, fully back-compat):
 *   `defaultSettings`  — seed settings for a new instance; plugins without it
 *                        have no settings and only ever run as a single default
 *                        instance.
 *   `settingsSchema`   — schema-driven settings descriptor; drives the per-
 *                        instance settings form in the Module Manager gear UI.
 */

import type { SystemState, SizeHint, RenderBudget } from '../state/types.js';

export const PLUGIN_CONTRACT_VERSION = 2;

// ─── Data sources (declarative) ───────────────────────────────────────────────

export type DataSourceSpec =
  | { kind: 'poll'; endpoint: string; intervalKey: 'board' | 'scout' | 'right' | 'suno' }
  | { kind: 'ws'; topic: 'board' | 'v1events' }
  | { kind: 'state' }; // driven purely by SystemState, no direct fetch needed

// ─── Rect ─────────────────────────────────────────────────────────────────────

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

// ─── Relevance result ─────────────────────────────────────────────────────────

export interface RelevanceResult {
  /** 0..100 — determines sort order + show/hide (< 10 → hidden). */
  priority: number;
  /** Requested cell size. */
  size: SizeHint;
  /** Optional area multiplier applied after size→span conversion. Default 1. */
  weight?: number;
}

// ─── PanelPlugin interface ────────────────────────────────────────────────────

// ─── Settings schema (P0 v-Next) ─────────────────────────────────────────────

/**
 * A single field descriptor in a plugin's settings schema.
 * The Module Manager renders a form from this — keeps all UI logic in one place.
 */
export type SettingsFieldType = 'string' | 'number' | 'boolean' | 'select';

export interface SettingsSelectOption {
  value: string;
  label: string;
}

export interface SettingsField {
  /** Machine key — used as the settings object property name. */
  readonly key: string;
  /** Human label shown in the settings form. */
  readonly label: string;
  readonly type: SettingsFieldType;
  /** Default value for new instances (should match `defaultSettings[key]`). */
  readonly default: string | number | boolean;
  /** Only relevant when type === 'select'. */
  readonly options?: readonly SettingsSelectOption[];
  /** Optional helper text shown below the field. */
  readonly hint?: string;
}

/** Full settings schema for a plugin type. */
export interface SettingsSchema {
  readonly fields: readonly SettingsField[];
}

// ─── PanelPlugin interface ────────────────────────────────────────────────────

export interface PanelPlugin {
  /** Unique identifier (kebab-case). */
  readonly id: string;
  /** Human label for the cell header. */
  readonly title: string;
  /** Declarative data sources — registry coalesces identical endpoints. */
  readonly dataSources: readonly DataSourceSpec[];

  /**
   * Pure fn: given the current SystemState, return how much this panel
   * wants to be visible. Called every state tick; must be cheap.
   */
  relevance(state: SystemState): RelevanceResult;

  /**
   * One-time DOM construction inside the allocated cell element.
   * Called once when the panel is first placed by the layout engine.
   * Must be idempotent (may be called again after suspend→resume if cell is re-created).
   */
  mount(el: HTMLElement): void;

  /**
   * Called every state tick with the governor's render budget.
   * Re-renders data; respects budget limits (nodes, fps, points, animate).
   */
  update(state: SystemState, budget: RenderBudget): void;

  /** Optional: resize canvases / re-layout when the cell geometry changes. */
  onResize?(rect: Rect): void;

  /** Optional: suspend timers, freeze sims, stop sparkline pushes (panel hidden/paused). */
  suspend?(): void;

  /** Optional: resume timers, restart sims. Called when panel becomes visible again. */
  resume?(): void;

  // ─── P0 v-Next additions (all optional; plugins without them are back-compat) ──

  /**
   * Seed settings for a new instance of this plugin type.
   * Plugins that omit this field have no configurable settings and only ever
   * run as a single default instance.
   */
  readonly defaultSettings?: Record<string, unknown>;

  /**
   * Describes every configurable field; drives the schema-driven settings form
   * in the Module Manager focus-mode gear.
   * Only meaningful when `defaultSettings` is also defined.
   */
  readonly settingsSchema?: SettingsSchema;
}
