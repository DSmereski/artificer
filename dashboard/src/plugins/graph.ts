/**
 * plugins/graph.ts — Knowledge-graph force-viz PanelPlugin (Phase C).
 *
 * Mounts a d3-force canvas from /v1/graph/god-nodes (seed, 60s cadence).
 * Click a node → fetches /v1/graph/neighbors and expands 1-hop.
 * Hover → explain tooltip (cheap: title only; /v1/graph/explain on demand).
 * suspend()/resume() stop/restart the sim.
 *
 * Budget compliance:
 *   graphMaxNodes=0  → hidden/gaming; canvas not drawn.
 *   graphMaxNodes=60 → busy tier (≤60 nodes, faster settle).
 *   graphMaxNodes=120→ idle tier (≤120 nodes, full drift).
 *
 * Auth: uses getBearerToken() from gateway.ts (same token the rest of /v1/* uses).
 */

import { register } from './registry.js';
import type { PanelPlugin, RelevanceResult, Rect } from './contract.js';
import type { SystemState, RenderBudget } from '../state/types.js';
import { getBearerToken, fetchV1 } from '../gateway.js';
import {
  createForceGraph,
  buildGraphFromGodNodes,
  buildGraphFromNeighbors,
  decideShouldRender,
  type ForceGraphInstance,
  type GraphData,
  type GodNode,
  type NeighborResponse,
} from '../charts/force.js';
import { escHtml } from '../format.js';

// ─── Relevance ────────────────────────────────────────────────────────────────

function relevance(state: SystemState): RelevanceResult {
  if (state.resources.gaming || state.tier === 'gaming') {
    return { priority: 5, size: 'hidden' };
  }
  // No bearer token (the wallpaper runs token-less) → the graph can never
  // render, so don't let it claim prime space for an "Auth needed"
  // placeholder. Hide it; an authed embed still shows it normally below.
  if (!getBearerToken()) {
    return { priority: 0, size: 'hidden' };
  }
  if (state.activity === 'idle') {
    return { priority: 70, size: 'lg' };
  }
  if (state.activity === 'building' || state.activity === 'reviewing') {
    return { priority: 30, size: 'sm' };
  }
  return { priority: 40, size: 'md' };
}

// ─── State ────────────────────────────────────────────────────────────────────

let _rootEl: HTMLElement | null       = null;
let _canvasWrap: HTMLElement | null   = null;
let _tooltip: HTMLElement | null      = null;
let _graph: ForceGraphInstance | null = null;
let _suspended = false;
let _lastMaxNodes = 0;
let _currentData: GraphData = { nodes: [], links: [] };
let _lastGodFetch = 0;
const GOD_FETCH_INTERVAL_MS = 60_000;

// ─── Tooltip ──────────────────────────────────────────────────────────────────

function _showTooltip(title: string, x: number, y: number): void {
  if (!_tooltip) return;
  _tooltip.textContent = title;
  _tooltip.style.left    = `${x + 12}px`;
  _tooltip.style.top     = `${y - 8}px`;
  _tooltip.style.display = 'block';
}

function _hideTooltip(): void {
  if (!_tooltip) return;
  _tooltip.style.display = 'none';
}

// ─── Fetch helpers ────────────────────────────────────────────────────────────

async function _fetchGodNodes(maxNodes: number): Promise<void> {
  try {
    const data = await fetchV1<GodNode[]>(`/graph/god-nodes?limit=${maxNodes}`);
    if (!data || !Array.isArray(data)) return;
    _currentData = buildGraphFromGodNodes(data, maxNodes);
    _lastGodFetch = Date.now();
    _graph?.setData(_currentData, maxNodes);
    _lastMaxNodes = maxNodes;
  } catch (err) {
    console.warn('[graph] god-nodes fetch failed', err);
  }
}

async function _expandNeighbors(slug: string, maxNodes: number): Promise<void> {
  try {
    const data = await fetchV1<NeighborResponse>(`/graph/neighbors?slug=${encodeURIComponent(slug)}&depth=1`);
    if (!data) return;
    _currentData = buildGraphFromNeighbors(_currentData, data, maxNodes);
    _graph?.setData(_currentData, maxNodes);
  } catch (err) {
    console.warn('[graph] neighbors fetch failed', err);
  }
}

async function _fetchExplain(slug: string): Promise<string | null> {
  try {
    const data = await fetchV1<{ slug: string; compiled_truth?: string; title?: string }>(
      `/graph/explain?slug=${encodeURIComponent(slug)}`
    );
    if (!data) return null;
    return data.compiled_truth ?? data.title ?? null;
  } catch {
    return null;
  }
}

// ─── Mount ────────────────────────────────────────────────────────────────────

function mount(el: HTMLElement): void {
  _rootEl = el;
  el.innerHTML = `
    <div class="panel-header">
      <span class="panel-label">KNOWLEDGE GRAPH</span>
      <span class="graph-node-count" id="graph-node-count"></span>
    </div>
    <div class="graph-canvas-wrap" id="graph-canvas-wrap"></div>
    <div class="graph-tooltip" id="graph-tooltip" style="display:none;position:fixed;pointer-events:none;z-index:999;background:var(--bg2);color:var(--dim);border:1px solid var(--copper);padding:4px 8px;font-size:11px;border-radius:3px;max-width:220px;"></div>
    <div class="graph-offline" id="graph-offline" style="display:none;">
      <span class="graph-ph-glyph">⬡</span>
      <span class="graph-ph-label">Auth needed for graph</span>
    </div>
  `;

  _canvasWrap = el.querySelector('#graph-canvas-wrap') as HTMLElement;
  _tooltip    = el.querySelector('#graph-tooltip') as HTMLElement;

  if (!_canvasWrap) return;

  const w = _canvasWrap.clientWidth  || 400;
  const h = _canvasWrap.clientHeight || 340;

  _graph = createForceGraph({
    width:  w,
    height: h,
    onSettled: () => {
      _updateNodeCount();
    },
    onNodeClick: (id, _title) => {
      if (_lastMaxNodes > 0) {
        void _expandNeighbors(id, _lastMaxNodes);
      }
    },
    onNodeHover: (id, title, x, y) => {
      if (id && title) {
        _showTooltip(escHtml(title), x, y);
        // Fire-and-forget explain fetch for richer tooltip
        void _fetchExplain(id).then((truth) => {
          if (truth) _showTooltip(escHtml(truth), x, y);
        });
      } else {
        _hideTooltip();
      }
    },
  });

  _canvasWrap.appendChild(_graph.canvas);
}

function _updateNodeCount(): void {
  const el = _rootEl?.querySelector('#graph-node-count') as HTMLElement | null;
  if (el) {
    const count = _currentData.nodes.length;
    el.textContent = count > 0 ? `${count} nodes` : '';
  }
}

// ─── Update ───────────────────────────────────────────────────────────────────

function update(state: SystemState, budget: RenderBudget): void {
  if (!_rootEl || !_graph) return;
  if (_suspended) return;

  const shouldRender = decideShouldRender(budget.graphMaxNodes);
  const offlineEl   = _rootEl.querySelector('#graph-offline') as HTMLElement | null;
  const wrapEl      = _rootEl.querySelector('#graph-canvas-wrap') as HTMLElement | null;

  // Show/hide offline or paused overlay
  if (!shouldRender) {
    if (offlineEl) offlineEl.style.display = 'flex';
    if (wrapEl)    wrapEl.style.display    = 'none';
    return;
  }

  if (!getBearerToken()) {
    if (offlineEl) {
      offlineEl.style.display = 'flex';
      const label = offlineEl.querySelector('.graph-ph-label') as HTMLElement | null;
      if (label) label.textContent = 'Auth needed for graph';
    }
    if (wrapEl) wrapEl.style.display = 'none';
    return;
  }

  if (offlineEl) offlineEl.style.display = 'none';
  if (wrapEl)    wrapEl.style.display    = '';

  const maxNodes = budget.graphMaxNodes;

  // Slow cadence god-node refresh (60s) or on max-node count change
  const now = Date.now();
  const needsGodFetch =
    _currentData.nodes.length === 0 ||
    now - _lastGodFetch > GOD_FETCH_INTERVAL_MS ||
    maxNodes !== _lastMaxNodes;

  if (needsGodFetch && !state.resources.gaming) {
    void _fetchGodNodes(maxNodes);
  }

  _updateNodeCount();
}

// ─── Resize ───────────────────────────────────────────────────────────────────

function onResize(rect: Rect): void {
  if (!_graph) return;
  _graph.resize(rect.w, rect.h - 36); // subtract header
}

// ─── Suspend / Resume ─────────────────────────────────────────────────────────

function suspend(): void {
  _suspended = true;
  _graph?.suspend();
  _hideTooltip();
}

function resume(): void {
  _suspended = false;
  _graph?.resume();
}

// ─── Plugin definition ────────────────────────────────────────────────────────

const graphPlugin: PanelPlugin = {
  id:          'graph',
  title:       'KNOWLEDGE GRAPH',
  dataSources: [
    { kind: 'poll', endpoint: '/v1/graph/god-nodes', intervalKey: 'right' },
    { kind: 'ws',   topic: 'v1events' },
  ],
  relevance,
  mount,
  update,
  onResize,
  suspend,
  resume,
};

register(graphPlugin);
export { graphPlugin };
