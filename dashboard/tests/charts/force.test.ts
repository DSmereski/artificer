/**
 * tests/charts/force.test.ts — Unit tests for the pure force-graph helpers.
 *
 * Tests: buildGraphFromGodNodes, buildGraphFromNeighbors, capNodes,
 *        decideShouldRender, and the ≤120 / ≤60 node cap logic.
 * No DOM required (node env). d3-force is not invoked here.
 */

import { describe, it, expect } from 'vitest';
import {
  buildGraphFromGodNodes,
  buildGraphFromNeighbors,
  capNodes,
  decideShouldRender,
  MAX_NODES_IDLE,
  MAX_NODES_BUSY,
  type GodNode,
  type GraphData,
  type NeighborResponse,
} from '../../src/charts/force.js';

// ─── Fixtures ────────────────────────────────────────────────────────────────

function makeGodNode(slug: string, degree = 5): GodNode {
  return { slug, title: `Title ${slug}`, kind: 'concept', degree, degree_in: 2, degree_out: 3 };
}

function makeGodNodes(n: number): GodNode[] {
  return Array.from({ length: n }, (_, i) => makeGodNode(`node-${i}`, n - i));
}

// ─── capNodes ────────────────────────────────────────────────────────────────

describe('capNodes', () => {
  it('returns all nodes when count ≤ maxNodes', () => {
    const nodes = makeGodNodes(10);
    const result = capNodes(nodes, 120);
    expect(result).toHaveLength(10);
  });

  it('caps to maxNodes preferring highest degree', () => {
    // Degrees are n-i, so node-0 has highest degree
    const nodes = makeGodNodes(150);
    const result = capNodes(nodes, 120);
    expect(result).toHaveLength(120);
    // Highest-degree node should be in the result
    expect(result.some((n) => n.slug === 'node-0')).toBe(true);
  });

  it('does not mutate the input array', () => {
    const nodes = makeGodNodes(130);
    const original = [...nodes];
    capNodes(nodes, 120);
    expect(nodes).toHaveLength(original.length);
    expect(nodes[0].slug).toBe(original[0].slug);
  });

  it('MAX_NODES_IDLE is 120', () => {
    expect(MAX_NODES_IDLE).toBe(120);
  });

  it('MAX_NODES_BUSY is 60', () => {
    expect(MAX_NODES_BUSY).toBe(60);
  });

  it('caps busy tier (≤60 nodes)', () => {
    const nodes = makeGodNodes(80);
    const result = capNodes(nodes, MAX_NODES_BUSY);
    expect(result).toHaveLength(60);
  });
});

// ─── decideShouldRender ──────────────────────────────────────────────────────

describe('decideShouldRender', () => {
  it('returns false when graphMaxNodes is 0 (gaming/offline)', () => {
    expect(decideShouldRender(0)).toBe(false);
  });

  it('returns true when graphMaxNodes > 0 (idle)', () => {
    expect(decideShouldRender(120)).toBe(true);
  });

  it('returns true when graphMaxNodes = 60 (busy)', () => {
    expect(decideShouldRender(60)).toBe(true);
  });

  it('returns false for negative values (guard)', () => {
    expect(decideShouldRender(-1)).toBe(false);
  });
});

// ─── buildGraphFromGodNodes ──────────────────────────────────────────────────

describe('buildGraphFromGodNodes', () => {
  it('builds nodes from god-nodes JSON', () => {
    const raw = makeGodNodes(5);
    const g = buildGraphFromGodNodes(raw, 120);
    expect(g.nodes).toHaveLength(5);
    expect(g.links).toHaveLength(0);
    expect(g.nodes[0].isGod).toBe(true);
    expect(g.nodes[0].isHub).toBe(false);
    expect(g.nodes[0].id).toBe('node-0');
    expect(g.nodes[0].title).toBe('Title node-0');
  });

  it('caps nodes at maxNodes', () => {
    const raw = makeGodNodes(150);
    const g = buildGraphFromGodNodes(raw, MAX_NODES_IDLE);
    expect(g.nodes).toHaveLength(MAX_NODES_IDLE);
  });

  it('caps at 60 in busy tier', () => {
    const raw = makeGodNodes(80);
    const g = buildGraphFromGodNodes(raw, MAX_NODES_BUSY);
    expect(g.nodes).toHaveLength(MAX_NODES_BUSY);
  });

  it('returns empty graph for empty input', () => {
    const g = buildGraphFromGodNodes([], 120);
    expect(g.nodes).toHaveLength(0);
    expect(g.links).toHaveLength(0);
  });

  it('preserves degree on nodes', () => {
    const raw = [makeGodNode('x', 42)];
    const g = buildGraphFromGodNodes(raw, 120);
    expect(g.nodes[0].degree).toBe(42);
  });
});

// ─── buildGraphFromNeighbors ─────────────────────────────────────────────────

describe('buildGraphFromNeighbors', () => {
  function seedGraph(slugs: string[]): GraphData {
    const nodes = slugs.map((s) => ({
      id: s, title: `T-${s}`, kind: 'concept', degree: 5, isGod: true, isHub: false,
    }));
    return { nodes, links: [] };
  }

  const mockNeighbors: NeighborResponse = {
    nodes: [
      { slug: 'new-a', title: 'New A', kind: 'concept' },
      { slug: 'new-b', title: 'New B', kind: 'concept' },
    ],
    edges: [
      { from: 'node-0', to: 'new-a', label: 'relates_to' },
      { from: 'new-a',  to: 'new-b' },
    ],
  };

  it('adds new nodes as hub nodes', () => {
    const existing = seedGraph(['node-0', 'node-1']);
    const result = buildGraphFromNeighbors(existing, mockNeighbors, 120);
    expect(result.nodes).toHaveLength(4);
    const newA = result.nodes.find((n) => n.id === 'new-a');
    expect(newA).toBeDefined();
    expect(newA!.isHub).toBe(true);
    expect(newA!.isGod).toBe(false);
  });

  it('does not duplicate existing nodes', () => {
    const existing = seedGraph(['node-0', 'new-a']); // new-a already exists
    const result = buildGraphFromNeighbors(existing, mockNeighbors, 120);
    // Only new-b should be added
    expect(result.nodes).toHaveLength(3);
    const dupes = result.nodes.filter((n) => n.id === 'new-a');
    expect(dupes).toHaveLength(1);
  });

  it('adds links between nodes in the capped set', () => {
    const existing = seedGraph(['node-0', 'node-1']);
    const result = buildGraphFromNeighbors(existing, mockNeighbors, 120);
    // Both edges reference nodes that should be in the result
    expect(result.links.length).toBeGreaterThan(0);
  });

  it('does not add links where one endpoint is outside the cap', () => {
    // Cap at 3 — only node-0, node-1, new-a fit; new-b is dropped
    const existing = seedGraph(['node-0', 'node-1']);
    const result = buildGraphFromNeighbors(existing, mockNeighbors, 3);
    expect(result.nodes).toHaveLength(3);
    // Link new-a→new-b should be dropped since new-b is not in the set
    const outOfBounds = result.links.some((l) => {
      const src = typeof l.source === 'string' ? l.source : l.source.id;
      const tgt = typeof l.target === 'string' ? l.target : l.target.id;
      return src === 'new-b' || tgt === 'new-b';
    });
    expect(outOfBounds).toBe(false);
  });

  it('does not mutate the existing graph', () => {
    const existing = seedGraph(['node-0', 'node-1']);
    const origLen = existing.nodes.length;
    buildGraphFromNeighbors(existing, mockNeighbors, 120);
    expect(existing.nodes).toHaveLength(origLen);
  });

  it('does not duplicate links', () => {
    const existing = seedGraph(['node-0', 'node-1']);
    // First expansion
    const step1 = buildGraphFromNeighbors(existing, mockNeighbors, 120);
    // Second expansion with same edges
    const step2 = buildGraphFromNeighbors(step1, mockNeighbors, 120);
    // Links should not be duplicated
    const edgeKeys = new Set(
      step2.links.map((l) => {
        const src = typeof l.source === 'string' ? l.source : l.source.id;
        const tgt = typeof l.target === 'string' ? l.target : l.target.id;
        return `${src}→${tgt}`;
      }),
    );
    expect(edgeKeys.size).toBe(step2.links.length); // no dupes
  });
});
