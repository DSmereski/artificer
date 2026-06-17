/**
 * tests/registry.test.ts — panel registry enable/disable (CC6).
 */

import { describe, it, expect, beforeEach } from 'vitest';
import {
  register,
  all,
  enabled,
  isPanelEnabled,
  setPanelEnabled,
  _clearForTest,
} from '../src/plugins/registry.js';
import type { PanelPlugin } from '../src/plugins/contract.js';

function stub(id: string): PanelPlugin {
  return {
    id,
    title: id,
    dataSources: [],
    relevance: () => ({ priority: 50, size: 'md' }),
    mount: () => {},
    update: () => {},
  };
}

describe('registry enable/disable', () => {
  beforeEach(() => {
    _clearForTest();
    // node test env has no localStorage → disabled set stays empty by default
    setPanelEnabled('a', true);
    setPanelEnabled('b', true);
    register(stub('a'));
    register(stub('b'));
    register(stub('c'));
  });

  it('panels default to enabled', () => {
    expect(isPanelEnabled('a')).toBe(true);
    expect(enabled().map((p) => p.id).sort()).toEqual(['a', 'b', 'c']);
  });

  it('disabling a panel removes it from enabled() but not all()', () => {
    setPanelEnabled('b', false);
    expect(isPanelEnabled('b')).toBe(false);
    expect(enabled().map((p) => p.id).sort()).toEqual(['a', 'c']);
    expect(all().map((p) => p.id).sort()).toEqual(['a', 'b', 'c']);
  });

  it('re-enabling restores it', () => {
    setPanelEnabled('b', false);
    setPanelEnabled('b', true);
    expect(enabled().map((p) => p.id)).toContain('b');
  });
});
