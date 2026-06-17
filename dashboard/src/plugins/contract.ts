/**
 * plugins/contract.ts — PanelPlugin interface contract (frozen in Pv2.1).
 *
 * Adding a panel = dropping a file that calls register({...}).
 * No edits to core files required when adding new panels.
 *
 * Version: 2 (bump on breaking changes; plugins should tolerate unknown budget fields).
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
}
