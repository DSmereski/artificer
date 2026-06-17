/**
 * tests/crew-board-full.test.ts — Full-board iframe plugin relevance + toggle.
 *
 * Node env (no DOM): the plugin guards document access, so activate/deactivate
 * are safe to call here. We assert the relevance state machine: hidden until
 * activated, hero when active, and hidden while gaming even if active.
 */

import { describe, it, expect, afterEach } from 'vitest';
import {
  crewBoardFullPlugin,
  activateFullBoard,
  deactivateFullBoard,
  isFullBoardActive,
} from '../src/plugins/crew-board-full.js';
import type { SystemState } from '../src/state/types.js';

function makeState(overrides: Partial<SystemState> = {}): SystemState {
  return {
    activity:    'idle',
    gatewayUp:   true,
    tasks:       { building: [], review: 0, qa: 0, ready: 0, done: 0 },
    escalations: { open: 0 },
    resources:   { gpus: [], cpuPct: 0, ramPct: 0, gaming: false, contended: false },
    counts:      { costUsd: 0, tokRateHive: 0, tokRateClaude: 0, parseFailRate: 0 },
    tier:        'idle',
    ts:          0,
    ...overrides,
  };
}

afterEach(() => {
  deactivateFullBoard(); // reset module state between tests
});

describe('crew-board-full relevance', () => {
  // v3 contract (P2): the board is ALWAYS embedded (a fixed grid slot), not a
  // toggle. relevance is `lg`/priority 90 whenever the gateway is up and not
  // gaming; the legacy activate/deactivate toggle no longer drives relevance.
  it('is a large cell by default (always embedded)', () => {
    const r = crewBoardFullPlugin.relevance(makeState());
    expect(r.size).toBe('lg');
    expect(r.priority).toBe(90);
  });

  it('stays large regardless of the legacy active toggle', () => {
    activateFullBoard();
    expect(crewBoardFullPlugin.relevance(makeState()).size).toBe('lg');
    deactivateFullBoard();
    expect(crewBoardFullPlugin.relevance(makeState()).size).toBe('lg');
  });

  it('hides while gaming (heavy nested renderer yields the GPU)', () => {
    const r = crewBoardFullPlugin.relevance(makeState({ tier: 'gaming' }));
    expect(r.size).toBe('hidden');
    expect(r.priority).toBe(0);
  });

  it('legacy toggle helpers are inert no-ops (board is permanently inline)', () => {
    // Kept so legacy callers (Esc handler, command palette) don't blank the
    // embedded kanban; they neither throw nor change relevance.
    expect(() => { activateFullBoard(); deactivateFullBoard(); }).not.toThrow();
    // _active is permanently true (board is always inline) and the no-op
    // helpers never change it or relevance.
    expect(isFullBoardActive()).toBe(true);
    expect(crewBoardFullPlugin.relevance(makeState()).size).toBe('lg');
  });
});
