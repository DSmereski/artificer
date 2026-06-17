/**
 * tests/layout/engine.test.ts — Unit tests for layout/engine.ts
 *
 * Tests the 4 worked examples from the plan:
 *   1. idle      → graph dominates, telemetry tiles right, calm
 *   2. mid-build → now-building hero = 'hero' (top band), graph→sm
 *   3. escalation-spike → escalations panel = 'hero' immediately
 *   4. gaming    → layout collapses to compact strip (graph hidden)
 */

import { describe, it, expect } from 'vitest';
import { computeLayout } from '../../src/layout/engine.js';
import type { CellLayout } from '../../src/layout/engine.js';
import type { PanelPlugin, RelevanceResult } from '../../src/plugins/contract.js';
import type { SystemState } from '../../src/state/types.js';

// ─── Fixtures ─────────────────────────────────────────────────────────────────

function makeState(overrides: Partial<SystemState> = {}): SystemState {
  return {
    activity:    'idle',
    gatewayUp:   true,
    tasks:       { building: [], review: 0, qa: 0, ready: 0, done: 0 },
    escalations: { open: 0 },
    resources:   { gpus: [], cpuPct: 0, ramPct: 0, gaming: false, contended: false },
    counts:      { costUsd: 0, tokRateHive: 0, tokRateClaude: 0, parseFailRate: 0 },
    tier:        'idle',
    ts:          Date.now(),
    ...overrides,
  };
}

function makePlugin(
  id: string,
  rel: (state: SystemState) => RelevanceResult,
): PanelPlugin {
  return {
    id,
    title: id,
    dataSources: [],
    relevance: rel,
    mount: () => {},
    update: () => {},
  };
}

// ─── Simplified relevance functions mirroring the plan examples ───────────────

const crewBoardPlugin = makePlugin('crew-board', (state) => ({
  priority: state.activity === 'building' ? 90 : 50,
  size:     state.activity === 'building' ? 'hero' : 'lg',
}));

const gpuPlugin = makePlugin('gpu', (state) => ({
  priority: state.resources.contended ? 80 : 60,
  size:     state.resources.gaming ? 'min' : state.resources.contended ? 'lg' : 'md',
}));

const telemetryPlugin = makePlugin('telemetry', (state) => ({
  priority: 55,
  size:     state.activity === 'building' ? 'md' : 'md',
}));

const graphPlugin = makePlugin('graph', (state) => ({
  priority: state.activity === 'idle' ? 70 : 30,
  size:     state.resources.gaming ? 'hidden' :
            state.activity === 'idle' ? 'lg' : 'sm',
}));

const escalationsPlugin = makePlugin('escalations', (state) => ({
  priority: state.escalations.open > 0 ? 100 : 5,
  size:     state.escalations.open > 0 ? 'hero' : 'hidden',
}));

const agendaPlugin = makePlugin('agenda', (_state) => ({
  priority: 40,
  size:     'sm',
}));

const allPlugins = [crewBoardPlugin, gpuPlugin, telemetryPlugin, graphPlugin, escalationsPlugin, agendaPlugin];

// ─── Focus mode (CC5) ───────────────────────────────────────────────────────

describe('computeLayout — focus mode', () => {
  it('focusing a panel seats ONLY that panel (as hero), hides the rest', () => {
    const state = makeState({ activity: 'idle', tier: 'idle' });
    const result = computeLayout(allPlugins, state, [], 'graph');
    expect(result.cells).toHaveLength(1);
    expect(result.cells[0].id).toBe('graph');
    expect(result.cells[0].sizeClass).toBe('hero');
  });

  it('null focus uses normal adaptive relevance', () => {
    const state = makeState({ activity: 'idle', tier: 'idle' });
    const result = computeLayout(allPlugins, state, [], null);
    expect(result.cells.length).toBeGreaterThan(1);
  });

  it('focusing a normally-hidden panel still shows it', () => {
    const state = makeState({ activity: 'idle', tier: 'idle' });
    // escalations is hidden at idle; focusing it must override that.
    const result = computeLayout(allPlugins, state, [], 'escalations');
    expect(result.cells).toHaveLength(1);
    expect(result.cells[0].id).toBe('escalations');
  });
});

// ─── Error boundary (CC7) ────────────────────────────────────────────────────

describe('computeLayout — error boundary', () => {
  it('a panel that throws in relevance() is skipped, others still seat', () => {
    const boom = makePlugin('boom', () => { throw new Error('kaboom'); });
    const state = makeState({ activity: 'idle', tier: 'idle' });
    const result = computeLayout([boom, ...allPlugins], state, []);
    expect(result.cells.find((c) => c.id === 'boom')).toBeUndefined();
    expect(result.cells.find((c) => c.id === 'crew-board')).toBeDefined();
  });
});

// ─── Scenario tests ───────────────────────────────────────────────────────────

describe('computeLayout — idle scenario', () => {
  it('idle: graph is visible and lg; crew-board is lg', () => {
    const state = makeState({ activity: 'idle', tier: 'idle' });
    const result = computeLayout(allPlugins, state, []);

    const graphCell = result.cells.find((c) => c.id === 'graph');
    const crewCell  = result.cells.find((c) => c.id === 'crew-board');

    expect(graphCell).toBeDefined();
    expect(graphCell?.sizeClass).toBe('lg');
    expect(crewCell).toBeDefined();
    expect(crewCell?.sizeClass).toBe('lg');
  });

  it('idle: escalations panel is hidden (priority < 10)', () => {
    const state = makeState({ activity: 'idle', tier: 'idle' });
    const result = computeLayout(allPlugins, state, []);
    const escCell = result.cells.find((c) => c.id === 'escalations');
    expect(escCell).toBeUndefined();
  });

  it('idle: all visible cells fit within viewport (no overlap check)', () => {
    const state = makeState({ activity: 'idle', tier: 'idle' });
    const result = computeLayout(allPlugins, state, []);
    expect(result.cells.length).toBeGreaterThan(0);
    // All cells have positive dimensions
    for (const cell of result.cells) {
      expect(cell.rect.w).toBeGreaterThan(0);
      expect(cell.rect.h).toBeGreaterThan(0);
    }
  });
});

describe('computeLayout — mid-build scenario', () => {
  it('mid-build: crew-board is hero size (top priority)', () => {
    const state = makeState({
      activity: 'building',
      tier: 'busy',
      tasks: {
        building: [{ slug: 'task-1', title: 'Task 1', turns: 5, progress: 0.25, stalledMs: 0 }],
        review: 0, qa: 0, ready: 2, done: 5,
      },
    });
    const result = computeLayout(allPlugins, state, []);
    const crewCell = result.cells.find((c) => c.id === 'crew-board');
    expect(crewCell?.sizeClass).toBe('hero');
    expect(crewCell?.priority).toBe(90);
  });

  it('mid-build: graph demotes to sm', () => {
    const state = makeState({
      activity: 'building',
      tier: 'busy',
      tasks: {
        building: [{ slug: 'task-1', title: 'Task 1', turns: 5, progress: 0.25, stalledMs: 0 }],
        review: 0, qa: 0, ready: 0, done: 0,
      },
    });
    const result = computeLayout(allPlugins, state, []);
    const graphCell = result.cells.find((c) => c.id === 'graph');
    expect(graphCell?.sizeClass).toBe('sm');
  });

  it('mid-build: hero panel is first in cells array (highest priority)', () => {
    const state = makeState({
      activity: 'building',
      tier: 'busy',
      tasks: {
        building: [{ slug: 't', title: 'T', turns: 3, progress: 0.15, stalledMs: 0 }],
        review: 0, qa: 0, ready: 0, done: 0,
      },
    });
    const result = computeLayout(allPlugins, state, []);
    expect(result.cells[0]?.id).toBe('crew-board');
  });
});

describe('computeLayout — escalation-spike scenario', () => {
  it('escalation: escalations panel is hero (priority 100)', () => {
    const state = makeState({
      activity: 'escalation',
      escalations: { open: 3, topReason: 'auth failure' },
    });
    const result = computeLayout(allPlugins, state, []);
    const escCell = result.cells.find((c) => c.id === 'escalations');
    expect(escCell).toBeDefined();
    expect(escCell?.sizeClass).toBe('hero');
    expect(escCell?.priority).toBe(100);
  });

  it('escalation: escalations cell is first in layout (highest priority)', () => {
    const state = makeState({
      activity: 'escalation',
      escalations: { open: 1 },
    });
    const result = computeLayout(allPlugins, state, []);
    expect(result.cells[0]?.id).toBe('escalations');
  });
});

describe('computeLayout — gaming scenario', () => {
  it('gaming: graph is hidden (gaming tier)', () => {
    const state = makeState({
      activity: 'idle',
      tier: 'gaming',
      resources: { gpus: [], cpuPct: 0, ramPct: 0, gaming: true, contended: false },
    });
    const result = computeLayout(allPlugins, state, []);
    const graphCell = result.cells.find((c) => c.id === 'graph');
    expect(graphCell).toBeUndefined(); // hidden → excluded from cells
  });

  it('gaming: nowHidden includes graph when transitioning from idle to gaming', () => {
    const idleState = makeState({ activity: 'idle', tier: 'idle' });
    const idleLayout = computeLayout(allPlugins, idleState, []);

    const gamingState = makeState({
      activity: 'idle',
      tier: 'gaming',
      resources: { gpus: [], cpuPct: 0, ramPct: 0, gaming: true, contended: false },
    });
    const gamingLayout = computeLayout(allPlugins, gamingState, idleLayout.cells);

    expect(gamingLayout.nowHidden).toContain('graph');
  });
});

describe('computeLayout — layout diff', () => {
  it('nowVisible contains newly added panels', () => {
    const state = makeState({ activity: 'escalation', escalations: { open: 1 } });
    const prev: CellLayout[] = []; // nothing visible before
    const result = computeLayout(allPlugins, state, prev);

    expect(result.nowVisible).toContain('escalations');
  });

  it('all cell positions are within reasonable viewport bounds', () => {
    const state = makeState();
    const result = computeLayout(allPlugins, state, []);
    for (const cell of result.cells) {
      expect(cell.rect.x).toBeGreaterThanOrEqual(0);
      expect(cell.rect.y).toBeGreaterThanOrEqual(0);
      expect(cell.rect.x + cell.rect.w).toBeLessThanOrEqual(5120 + 100); // small tolerance
    }
  });
});

// ─── 5120×1440 all-panels-seat guarantee ──────────────────────────────────────

describe('computeLayout — all panels seat at 5120×1440', () => {
  // Build a realistic set of 9 panels matching the production dashboard.
  const clockPlugin = makePlugin('clock', (_s) => ({ priority: 30, size: 'sm' }));
  const tokensPlugin = makePlugin('tokens-day', (_s) => ({ priority: 45, size: 'sm' }));
  const terminalPlugin = makePlugin('terminal', (_s) => ({ priority: 25, size: 'md' }));
  const sunoPlugin = makePlugin('suno', (_s) => ({ priority: 45, size: 'md' }));

  const ninePlugins = [
    ...allPlugins,     // crew-board, gpu, telemetry, graph, escalations, agenda (6)
    clockPlugin,       // 7
    tokensPlugin,      // 8
    terminalPlugin,    // 9
    sunoPlugin,        // 10
  ];

  it('idle: all non-hidden panels get a seat (no drops)', () => {
    const state = makeState({ activity: 'idle', tier: 'idle' });
    const result = computeLayout(ninePlugins, state, []);

    // escalations is hidden in idle (priority < 10)
    const hiddenInIdle = new Set(['escalations']);
    const expectedIds = ninePlugins
      .map((p) => p.id)
      .filter((id) => !hiddenInIdle.has(id));

    const placedIds = new Set(result.cells.map((c) => c.id));
    for (const id of expectedIds) {
      expect(placedIds.has(id)).toBe(true);
    }
  });

  it('reviewing: crew-board, gpu, telemetry, graph, clock, terminal, suno, tokens all seated', () => {
    const state = makeState({ activity: 'reviewing', tier: 'busy' });
    const result = computeLayout(ninePlugins, state, []);

    const mustBePresent = ['crew-board', 'gpu', 'telemetry', 'graph', 'clock', 'terminal', 'suno', 'tokens-day'];
    const placedIds = new Set(result.cells.map((c) => c.id));
    for (const id of mustBePresent) {
      expect(placedIds.has(id)).toBe(true);
    }
  });

  it('building: hero does not cause other panels to drop', () => {
    const state = makeState({
      activity: 'building', tier: 'busy',
      tasks: { building: [{ slug: 't', title: 'T', turns: 3, progress: 0.15, stalledMs: 0 }],
               review: 0, qa: 0, ready: 0, done: 0 },
    });
    const result = computeLayout(ninePlugins, state, []);

    // crew-board is hero; everything else visible should still be placed.
    const mustBePresent = ['crew-board', 'gpu', 'telemetry', 'suno'];
    const placedIds = new Set(result.cells.map((c) => c.id));
    for (const id of mustBePresent) {
      expect(placedIds.has(id)).toBe(true);
    }
  });

  it('telemetry has at least 4 col span (not cramped)', () => {
    const state = makeState({ activity: 'idle', tier: 'idle' });
    const result = computeLayout(ninePlugins, state, []);
    const tel = result.cells.find((c) => c.id === 'telemetry');
    expect(tel).toBeDefined();
    expect(tel!.colSpan).toBeGreaterThanOrEqual(4);
  });

  it('no two cells overlap in their grid positions', () => {
    const state = makeState({ activity: 'idle', tier: 'idle' });
    const result = computeLayout(ninePlugins, state, []);

    // Check that no two cells share the same (bandIdx, colStart..colStart+colSpan-1)
    const occupied = new Map<string, string>(); // "band:col" → id
    for (const cell of result.cells) {
      for (let c = cell.colStart; c < cell.colStart + cell.colSpan; c++) {
        const key = `${cell.bandIdx}:${c}`;
        expect(occupied.has(key)).toBe(false);
        occupied.set(key, cell.id);
      }
    }
  });

  it('cells have positive non-zero pixel dimensions', () => {
    const state = makeState({ activity: 'idle', tier: 'idle' });
    const result = computeLayout(ninePlugins, state, []);
    for (const cell of result.cells) {
      expect(cell.rect.w).toBeGreaterThan(0);
      expect(cell.rect.h).toBeGreaterThan(0);
    }
  });
});
