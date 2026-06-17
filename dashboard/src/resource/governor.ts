/**
 * resource/governor.ts — Maps resource state to a RenderTier + RenderBudget.
 *
 * Reads ONLY the two 5060 Ti cards (is5060 flag) + cpu/ram.
 * The 4080 contributes ONLY the gaming boolean.
 *
 * Hysteresis bands prevent tier flapping:
 *   Enter busy:   5060 Ti util > 70  OR  temp > 70
 *   Leave busy:   5060 Ti util < 60  AND temp < 60
 *   Enter gaming: 5060 Ti util > 85  OR  temp > 78  OR  vramFree < 1500MB  OR gaming flag
 *   Leave gaming: util < 70  AND temp < 65  AND vramFree > 2000MB  AND !gaming
 *   Offline:      !gatewayUp (instant, no hysteresis)
 *
 * Unit-tested: deriveTierWithHysteresis asserts the 5060-vs-4080 split.
 */

import type { GpuInfo } from '../types.js';
import type { RenderTier, RenderBudget } from '../state/types.js';
import { is5060Ti } from '../state/derive.js';

// ─── Hysteresis thresholds ────────────────────────────────────────────────────

const THRESHOLDS = {
  busyEnterUtil:    70,
  busyEnterTemp:    70,
  busyLeaveUtil:    60,
  busyLeaveTemp:    60,

  gamingEnterUtil:  85,
  gamingEnterTemp:  78,
  gamingEnterVramFree: 1500, // MB
  gamingLeaveUtil:  70,
  gamingLeaveTemp:  65,
  gamingLeaveVramFree: 2000, // MB
} as const;

// ─── Budget table ─────────────────────────────────────────────────────────────

const BUDGETS: Record<RenderTier, RenderBudget> = {
  idle: {
    graphFps:        60,
    graphMaxNodes:   120,
    chartFps:        60,
    chartMaxPoints:  120,
    animate:         true,
    scoutMs:         3_000,
    boardMs:         10_000,
    ws:              'all',
  },
  busy: {
    graphFps:        30,
    graphMaxNodes:   60,
    chartFps:        30,
    chartMaxPoints:  60,
    animate:         false,
    scoutMs:         5_000,
    boardMs:         10_000,
    ws:              'all',
  },
  gaming: {
    graphFps:        0,
    graphMaxNodes:   0,
    chartFps:        0,
    chartMaxPoints:  0,
    animate:         false,
    scoutMs:         15_000,
    boardMs:         20_000,
    ws:              'board',
  },
  offline: {
    graphFps:        0,
    graphMaxNodes:   0,
    chartFps:        0,
    chartMaxPoints:  0,
    animate:         false,
    scoutMs:         30_000,
    boardMs:         30_000,
    ws:              'none',
  },
};

// ─── Governor (stateful — holds hysteresis) ───────────────────────────────────

export interface Governor {
  /** Update with new GPU data; returns the new tier. */
  update(gpus: GpuInfo[], gaming: boolean, gatewayUp: boolean): RenderTier;
  /** Current tier. */
  currentTier(): RenderTier;
  /** Budget for the current tier. */
  budget(): RenderBudget;
}

function createGovernor(): Governor {
  let _tier: RenderTier = 'offline';

  function update(gpus: GpuInfo[], gaming: boolean, gatewayUp: boolean): RenderTier {
    if (!gatewayUp) {
      _tier = 'offline';
      return _tier;
    }

    // Separate the two 5060 Tis from the 4080
    // The 4080 contributes ONLY the gaming flag — never drives the tier.
    const ti5060s = gpus.filter((g) => is5060Ti(g.name));

    const vramTotals = ti5060s.map((g) => g.vram_total_mb ?? 0);
    const vramUseds  = ti5060s.map((g) => g.vram_used_mb  ?? 0);
    const vramFrees  = ti5060s.map((_g, i) => Math.max(0, vramTotals[i] - vramUseds[i]));

    const maxUtil    = ti5060s.length > 0 ? Math.max(...ti5060s.map((g) => g.utilization_pct)) : 0;
    const maxTemp    = ti5060s.length > 0 ? Math.max(...ti5060s.map((g) => g.temp_c))          : 0;
    const minVramFree = vramFrees.length > 0 ? Math.min(...vramFrees)                           : Infinity;

    // Hysteresis transitions
    if (_tier === 'gaming') {
      // Leave gaming only if ALL conditions are below leave thresholds AND !gaming
      const canLeave =
        !gaming &&
        maxUtil    < THRESHOLDS.gamingLeaveUtil &&
        maxTemp    < THRESHOLDS.gamingLeaveTemp &&
        minVramFree > THRESHOLDS.gamingLeaveVramFree;
      if (canLeave) {
        // Demote to busy or idle
        const stillBusy =
          maxUtil >= THRESHOLDS.busyEnterUtil ||
          maxTemp >= THRESHOLDS.busyEnterTemp;
        _tier = stillBusy ? 'busy' : 'idle';
      }
      // else: stay gaming
    } else if (_tier === 'busy') {
      // Enter gaming ONLY when the 4080 is actually running a game. The 5060
      // Ti AI GPUs are maxed during every hive build (qwen resident), so
      // letting their util/temp/vram enter the gaming tier made "building"
      // look like "gaming" and threw the wallpaper into the throttled,
      // panels-hidden view. AI-GPU load stays in the 'busy' tier (panels
      // visible, lighter render budget); gaming = a real game only.
      const enterGaming = gaming;
      if (enterGaming) {
        _tier = 'gaming';
      } else {
        // Leave busy?
        const leaveBusy =
          maxUtil < THRESHOLDS.busyLeaveUtil &&
          maxTemp < THRESHOLDS.busyLeaveTemp;
        if (leaveBusy) _tier = 'idle';
      }
    } else {
      // Currently idle or offline. Gaming only on a real 4080 game (see above);
      // AI 5060 Ti load promotes to 'busy', not 'gaming'.
      const enterGaming = gaming;
      if (enterGaming) {
        _tier = 'gaming';
      } else {
        const enterBusy =
          maxUtil >= THRESHOLDS.busyEnterUtil ||
          maxTemp >= THRESHOLDS.busyEnterTemp;
        if (enterBusy) _tier = 'busy';
        else           _tier = 'idle';
      }
    }

    return _tier;
  }

  function currentTier(): RenderTier { return _tier; }
  function budget(): RenderBudget    { return BUDGETS[_tier]; }

  return { update, currentTier, budget };
}

/** Singleton governor instance. */
export const governor = createGovernor();

/** Get the RenderBudget for a given tier (pure, for unit tests). */
export function budgetForTier(tier: RenderTier): RenderBudget {
  return BUDGETS[tier];
}

/**
 * Pure (no hysteresis) tier derivation from GPU data.
 * Used in unit tests to assert the GPU split logic.
 *
 * IMPORTANT: The 4080 must NEVER drive a non-gaming tier by itself.
 * This function asserts that: if only the 4080's util is high but gaming=false,
 * the tier should be idle (4080 is not tiring the render budget).
 */
export function deriveTierFromGpus(
  gpus: GpuInfo[],
  gaming: boolean,
  gatewayUp: boolean,
): RenderTier {
  if (!gatewayUp) return 'offline';
  if (gaming) return 'gaming';

  const ti5060s = gpus.filter((g) => is5060Ti(g.name));

  if (ti5060s.length === 0) return 'idle'; // no 5060 Tis detected yet

  const vramFrees = ti5060s.map((gpu) => {
    const total = gpu.vram_total_mb ?? 0;
    const used  = gpu.vram_used_mb  ?? 0;
    return Math.max(0, total - used);
  });

  const maxUtil    = Math.max(...ti5060s.map((gpu) => gpu.utilization_pct));
  const maxTemp    = Math.max(...ti5060s.map((gpu) => gpu.temp_c));
  const minVramFree = Math.min(...vramFrees);

  if (
    maxUtil    >= THRESHOLDS.gamingEnterUtil ||
    maxTemp    >= THRESHOLDS.gamingEnterTemp ||
    minVramFree <= THRESHOLDS.gamingEnterVramFree
  ) return 'gaming';

  if (
    maxUtil >= THRESHOLDS.busyEnterUtil ||
    maxTemp >= THRESHOLDS.busyEnterTemp
  ) return 'busy';

  return 'idle';
}
