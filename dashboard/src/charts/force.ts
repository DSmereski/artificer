/**
 * charts/force.ts — d3-force knowledge-graph rendered on a 2D canvas.
 *
 * Design contract:
 *   - Builds nodes/links from /v1/graph/god-nodes JSON (+ neighbor expansion).
 *   - Caps total nodes at MAX_NODES (120 idle / 60 busy).
 *   - Runs the simulation to settle (alpha ≈ 0) then STOPS — no perpetual draw.
 *   - Budget-aware: gaming/paused → no render, no sim.
 *   - Honeycomb/amber node styling per DESIGN.md.
 *
 * Exported pure helpers (unit-testable):
 *   buildGraphFromGodNodes, buildGraphFromNeighbors, capNodes, decideShouldRender
 */

import {
  forceSimulation,
  forceLink,
  forceManyBody,
  forceCenter,
  forceCollide,
  type Simulation,
  type SimulationNodeDatum,
  type SimulationLinkDatum,
} from 'd3-force';

// ─── Public types ─────────────────────────────────────────────────────────────

/** Shape returned by /v1/graph/god-nodes */
export interface GodNode {
  slug: string;
  title: string;
  kind:  string;
  degree:     number;
  degree_in:  number;
  degree_out: number;
}

/** Shape returned by /v1/graph/neighbors */
export interface NeighborResponse {
  nodes: Array<{ slug: string; title: string; kind: string }>;
  edges: Array<{ from: string; to: string; label?: string }>;
}

export interface GraphNode extends SimulationNodeDatum {
  id:       string;
  title:    string;
  kind:     string;
  degree:   number;
  isGod:    boolean;   // came from god-nodes seed
  isHub:    boolean;   // expanded neighbor of a selected node
}

export interface GraphLink extends SimulationLinkDatum<GraphNode> {
  source: GraphNode | string;
  target: GraphNode | string;
  label?: string;
}

export interface GraphData {
  nodes: GraphNode[];
  links: GraphLink[];
}

// ─── Max-node cap ─────────────────────────────────────────────────────────────

export const MAX_NODES_IDLE  = 120;
export const MAX_NODES_BUSY  = 60;

// ─── Pure helpers (unit-testable) ─────────────────────────────────────────────

/**
 * Build a GraphData from /v1/graph/god-nodes response.
 * Nodes only (no edges from god-nodes endpoint).
 */
export function buildGraphFromGodNodes(nodes: GodNode[], maxNodes: number): GraphData {
  const capped = capNodes(nodes, maxNodes);
  const graphNodes: GraphNode[] = capped.map((n) => ({
    id:     n.slug,
    title:  n.title,
    kind:   n.kind,
    degree: n.degree,
    isGod:  true,
    isHub:  false,
  }));
  return { nodes: graphNodes, links: [] };
}

/**
 * Merge a neighbor expansion response into an existing GraphData.
 * Returns a NEW GraphData (immutable pattern).
 * New nodes are flagged isHub=true. Caps total at maxNodes.
 */
export function buildGraphFromNeighbors(
  existing: GraphData,
  response: NeighborResponse,
  maxNodes: number,
): GraphData {
  const existingIds = new Set(existing.nodes.map((n) => n.id));

  // Add new nodes (those not already present)
  const newNodes: GraphNode[] = response.nodes
    .filter((n) => !existingIds.has(n.slug))
    .map((n) => ({
      id:     n.slug,
      title:  n.title,
      kind:   n.kind,
      degree: 1,
      isGod:  false,
      isHub:  true,
    }));

  const allNodes = [...existing.nodes, ...newNodes];
  const capped   = allNodes.slice(0, maxNodes);
  const cappedIds = new Set(capped.map((n) => n.id));

  // Merge links — only include links where BOTH ends are in the capped set
  const existingLinkKeys = new Set(
    existing.links.map((l) => `${linkId(l.source)}→${linkId(l.target)}`),
  );

  const newLinks: GraphLink[] = response.edges
    .filter(
      (e) =>
        cappedIds.has(e.from) &&
        cappedIds.has(e.to) &&
        !existingLinkKeys.has(`${e.from}→${e.to}`),
    )
    .map((e) => ({ source: e.from, target: e.to, label: e.label }));

  return {
    nodes: capped,
    links: [...existing.links, ...newLinks],
  };
}

function linkId(endpoint: GraphNode | string): string {
  return typeof endpoint === 'string' ? endpoint : endpoint.id;
}

/**
 * Cap a list of nodes at maxNodes, preferring higher-degree nodes.
 * Pure — returns a new array.
 */
export function capNodes(nodes: GodNode[], maxNodes: number): GodNode[] {
  if (nodes.length <= maxNodes) return [...nodes];
  return [...nodes]
    .sort((a, b) => b.degree - a.degree)
    .slice(0, maxNodes);
}

/**
 * Given the current budget (graphMaxNodes), decide whether the graph
 * should render at all.
 *   graphMaxNodes === 0 → do not render (gaming/offline)
 *   graphMaxNodes  > 0 → render, cap at graphMaxNodes
 */
export function decideShouldRender(graphMaxNodes: number): boolean {
  return graphMaxNodes > 0;
}

// ─── CSS-var reader ───────────────────────────────────────────────────────────

function cssHex(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/** Read graph canvas colors from CSS theme vars at draw-time. */
function getGraphColors() {
  const copper = cssHex('--hex-copper', '#c07840');
  const amber  = cssHex('--hex-amber',  '#e0a030');
  const bg     = cssHex('--hex-bg',     '#121610');
  const line   = cssHex('--hex-line',   '#363c30');
  const dim    = cssHex('--hex-dim',    '#b8b4a8');
  return {
    bg,
    nodeGod:    copper,
    nodeHub:    _darken(copper, 0.55),
    nodeStroke: amber,
    linkLine:   _darken(line, 0.7),
    labelText:  dim,
    hoverRing:  cssHex('--hex-amber', '#e0a030'),
    selectFill: amber,
  };
}

/** Simple lightness scale — multiply RGB channels by factor (0=black, 1=same). */
function _darken(hex: string, factor: number): string {
  const m = hex.replace('#', '').match(/.{2}/g);
  if (!m) return hex;
  const [r, g, b] = m.map(x => Math.round(parseInt(x, 16) * factor));
  return `#${r.toString(16).padStart(2,'0')}${g.toString(16).padStart(2,'0')}${b.toString(16).padStart(2,'0')}`;
}

const NODE_RADIUS_GOD  = 10;
const NODE_RADIUS_HUB  = 7;
const ALPHA_DECAY      = 0.028;  // settle in ~100 ticks
const ALPHA_MIN        = 0.001;  // stop threshold
const VELOCITY_DECAY   = 0.4;

// ─── ForceGraph class ─────────────────────────────────────────────────────────

export interface ForceGraphOptions {
  /** Width of the canvas in px. */
  width: number;
  /** Height of the canvas in px. */
  height: number;
  /** Called when the sim settles. */
  onSettled?: () => void;
  /** Called when a node is clicked. Returns the node id. */
  onNodeClick?: (id: string, title: string) => void;
  /** Called on node hover with node id (null = unhover). */
  onNodeHover?: (id: string | null, title: string | null, x: number, y: number) => void;
}

export interface ForceGraphInstance {
  setData(data: GraphData, maxNodes: number): void;
  resize(w: number, h: number): void;
  suspend(): void;
  resume(): void;
  destroy(): void;
  canvas: HTMLCanvasElement;
}

/**
 * Create a ForceGraph rendering to a new HTMLCanvasElement.
 * Caller appends `instance.canvas` to the DOM.
 */
export function createForceGraph(opts: ForceGraphOptions): ForceGraphInstance {
  const canvas  = document.createElement('canvas');
  canvas.width  = opts.width;
  canvas.height = opts.height;
  canvas.style.cssText = 'display:block;width:100%;height:100%;cursor:default;';

  const ctx = canvas.getContext('2d')!;

  let _nodes:   GraphNode[] = [];
  let _links:   GraphLink[] = [];
  let _sim:     Simulation<GraphNode, GraphLink> | null = null;
  let _paused   = false;
  let _settled  = false;
  let _rafId:   number | null = null;
  let _hoverId: string | null = null;
  let _selectId: string | null = null;

  // ── Simulation lifecycle ────────────────────────────────────────────────────

  function _startSim(nodes: GraphNode[], links: GraphLink[], w: number, h: number): void {
    // Stop previous sim
    _sim?.stop();
    if (_rafId !== null) { cancelAnimationFrame(_rafId); _rafId = null; }

    _settled = false;

    // d3-force requires mutable node objects (adds x, y, vx, vy).
    // We work with a mutable copy internally.
    _nodes = nodes.map((n) => ({ ...n }));

    // Resolve link references to node objects
    const nodeById = new Map(_nodes.map((n) => [n.id, n]));
    _links = links.map((l) => ({
      ...l,
      source: nodeById.get(linkId(l.source)) ?? linkId(l.source),
      target: nodeById.get(linkId(l.target)) ?? linkId(l.target),
    }));

    _sim = forceSimulation<GraphNode, GraphLink>(_nodes)
      .force('link',    forceLink<GraphNode, GraphLink>(_links)
                          .id((d) => d.id)
                          .distance(80)
                          .strength(0.4))
      .force('charge',  forceManyBody().strength(-180))
      .force('center',  forceCenter(w / 2, h / 2))
      .force('collide', forceCollide<GraphNode>()
                          .radius((d) => nodeRadius(d) + 4))
      .alphaDecay(ALPHA_DECAY)
      .velocityDecay(VELOCITY_DECAY)
      .alphaMin(ALPHA_MIN)
      .on('end', () => {
        _settled = true;
        _draw(w, h);            // final frame
        opts.onSettled?.();
        // Sim has stopped itself via alphaMin; cancel any pending rAF
        if (_rafId !== null) { cancelAnimationFrame(_rafId); _rafId = null; }
      });

    // Kick off render loop
    if (!_paused) _scheduleRaf(w, h);
  }

  function _scheduleRaf(w: number, h: number): void {
    if (_paused || _settled || _rafId !== null) return;
    _rafId = requestAnimationFrame(() => {
      _rafId = null;
      if (!_paused) {
        _draw(w, h);
        if (!_settled) _scheduleRaf(w, h);
      }
    });
  }

  // ── Drawing ─────────────────────────────────────────────────────────────────

  function nodeRadius(n: GraphNode): number {
    if (n.isGod) return NODE_RADIUS_GOD + Math.min(4, n.degree * 0.3);
    return NODE_RADIUS_HUB;
  }

  function _draw(w: number, h: number): void {
    const COLORS = getGraphColors();
    ctx.clearRect(0, 0, w, h);

    // Background
    ctx.fillStyle = COLORS.bg;
    ctx.fillRect(0, 0, w, h);

    // Links
    ctx.save();
    ctx.strokeStyle = COLORS.linkLine;
    ctx.lineWidth   = 1;
    ctx.globalAlpha = 0.6;
    for (const link of _links) {
      const src = link.source as GraphNode;
      const tgt = link.target as GraphNode;
      if (typeof src === 'string' || typeof tgt === 'string') continue;
      if (src.x == null || src.y == null || tgt.x == null || tgt.y == null) continue;
      ctx.beginPath();
      ctx.moveTo(src.x, src.y);
      ctx.lineTo(tgt.x, tgt.y);
      ctx.stroke();
    }
    ctx.restore();

    // Nodes
    for (const node of _nodes) {
      if (node.x == null || node.y == null) continue;
      const r      = nodeRadius(node);
      const isHov  = node.id === _hoverId;
      const isSel  = node.id === _selectId;
      const fill   = isSel ? COLORS.selectFill : node.isGod ? COLORS.nodeGod : COLORS.nodeHub;
      const stroke = isHov ? COLORS.hoverRing : COLORS.nodeStroke;

      ctx.save();
      ctx.globalAlpha = isSel ? 1.0 : isHov ? 0.95 : 0.85;

      // Node body (hex approximation with rounded circle)
      ctx.beginPath();
      ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
      ctx.fillStyle = fill;
      ctx.fill();

      // Ring
      ctx.beginPath();
      ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
      ctx.strokeStyle = stroke;
      ctx.lineWidth   = isHov || isSel ? 2 : 1;
      ctx.stroke();

      ctx.restore();

      // Label (short slug)
      if (r >= NODE_RADIUS_GOD) {
        ctx.save();
        ctx.fillStyle    = COLORS.labelText;  // resolved above
        ctx.font         = '9px "Inter", monospace';
        ctx.textAlign    = 'center';
        ctx.textBaseline = 'top';
        ctx.globalAlpha  = 0.8;
        const label = node.title.length > 14 ? node.title.slice(0, 13) + '…' : node.title;
        ctx.fillText(label, node.x, node.y + r + 2);
        ctx.restore();
      }
    }
  }

  // ── Hit-test ─────────────────────────────────────────────────────────────────

  function _nodeAt(cx: number, cy: number): GraphNode | null {
    for (const node of _nodes) {
      if (node.x == null || node.y == null) continue;
      const r  = nodeRadius(node) + 4; // enlarged hit area
      const dx = cx - node.x;
      const dy = cy - node.y;
      if (dx * dx + dy * dy <= r * r) return node;
    }
    return null;
  }

  // ── Event listeners ──────────────────────────────────────────────────────────

  function _canvasXY(e: MouseEvent): { cx: number; cy: number } {
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width  / rect.width;
    const scaleY = canvas.height / rect.height;
    return {
      cx: (e.clientX - rect.left) * scaleX,
      cy: (e.clientY - rect.top)  * scaleY,
    };
  }

  canvas.addEventListener('mousemove', (e) => {
    const { cx, cy } = _canvasXY(e);
    const node = _nodeAt(cx, cy);
    const newId = node?.id ?? null;
    if (newId !== _hoverId) {
      _hoverId = newId;
      canvas.style.cursor = newId ? 'pointer' : 'default';
      opts.onNodeHover?.(newId, node?.title ?? null, e.clientX, e.clientY);
      // Redraw to update hover styling (cheap single frame)
      _draw(canvas.width, canvas.height);
    }
  });

  canvas.addEventListener('mouseleave', () => {
    if (_hoverId !== null) {
      _hoverId = null;
      canvas.style.cursor = 'default';
      opts.onNodeHover?.(null, null, 0, 0);
      _draw(canvas.width, canvas.height);
    }
  });

  canvas.addEventListener('click', (e) => {
    const { cx, cy } = _canvasXY(e);
    const node = _nodeAt(cx, cy);
    if (node) {
      _selectId = node.id === _selectId ? null : node.id; // toggle
      opts.onNodeClick?.(node.id, node.title);
      _draw(canvas.width, canvas.height);
    } else {
      // Click empty → deselect
      _selectId = null;
      _draw(canvas.width, canvas.height);
    }
  });

  // ── Public interface ─────────────────────────────────────────────────────────

  function setData(data: GraphData, maxNodes: number): void {
    const capped: GraphData = {
      nodes: data.nodes.slice(0, maxNodes),
      links: data.links,
    };
    _startSim(capped.nodes, capped.links, canvas.width, canvas.height);
  }

  function resize(w: number, h: number): void {
    canvas.width  = w;
    canvas.height = h;
    if (_sim) {
      _sim.force('center', forceCenter(w / 2, h / 2));
      // Reheat slightly so nodes adjust
      if (_settled) {
        _settled = false;
        _sim.alpha(0.1).restart();
        _scheduleRaf(w, h);
      }
    }
    _draw(w, h);
  }

  function suspend(): void {
    _paused = true;
    _sim?.stop();
    if (_rafId !== null) { cancelAnimationFrame(_rafId); _rafId = null; }
  }

  function resume(): void {
    _paused = false;
    if (!_settled && _sim) {
      _sim.restart();
      _scheduleRaf(canvas.width, canvas.height);
    } else {
      _draw(canvas.width, canvas.height);
    }
  }

  function destroy(): void {
    suspend();
    _sim?.stop();
    _sim = null;
    _nodes = [];
    _links = [];
    window.removeEventListener('hive-theme-change', _onThemeChange);
  }

  // Re-draw on theme switch so canvas picks up new colors immediately.
  function _onThemeChange() {
    _draw(canvas.width, canvas.height);
  }
  window.addEventListener('hive-theme-change', _onThemeChange);

  return { setData, resize, suspend, resume, destroy, canvas };
}
