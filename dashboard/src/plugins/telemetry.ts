/**
 * plugins/telemetry.ts — Telemetry wall PanelPlugin wrapper.
 *
 * Wraps panels/telemetry.ts (initTelemetryPanel, updateTelemetryCharts, etc.).
 * md always; demotes low-value charts in busy/gaming tier.
 * Honors budget: chartFps, chartMaxPoints.
 */

import { register } from './registry.js';
import type { PanelPlugin, RelevanceResult, Rect } from './contract.js';
import type { SystemState, RenderBudget } from '../state/types.js';
import {
  initTelemetryPanel,
  updateTelemetryCharts,
} from '../panels/telemetry.js';
import type { BoardStats } from '../gateway.js';
import type { BoardStatsSample } from '../types.js';
import { pushSample } from '../format.js';

// ─── Relevance ────────────────────────────────────────────────────────────────

function relevance(state: SystemState): RelevanceResult {
  if (state.tier === 'gaming') {
    return { priority: 30, size: 'min' };
  }
  if (state.activity === 'building') {
    return { priority: 55, size: 'md' };
  }
  return { priority: 55, size: 'md' };
}

// ─── State ────────────────────────────────────────────────────────────────────

let _mounted = false;
let _suspended = false;
let _rollingBuf: BoardStatsSample[] = [];

// ─── Mount ────────────────────────────────────────────────────────────────────

function mount(el: HTMLElement): void {
  // Create container divs that the existing telemetry panel expects
  el.innerHTML = `
    <div class="panel-header">
      <span class="panel-label">TELEMETRY</span>
    </div>
    <div id="telemetry-charts" class="telemetry-charts-wrap"></div>
  `;

  if (!_mounted) {
    initTelemetryPanel();
    _mounted = true;
  }
}

// ─── Update ───────────────────────────────────────────────────────────────────

function update(_state: SystemState, budget: RenderBudget): void {
  if (_suspended) return;

  // Update with rolling buffer if we have data
  if (_rollingBuf.length > 0) {
    // Apply budget: trim buffer to chartMaxPoints
    const maxPts = budget.chartMaxPoints;
    const buf = maxPts > 0 && _rollingBuf.length > maxPts
      ? _rollingBuf.slice(_rollingBuf.length - maxPts)
      : _rollingBuf;

    if (budget.chartFps > 0) {
      updateTelemetryCharts(buf);
    }
  }
}

/** Called by sources.ts when new board stats arrive. */
export function onBoardStats(stats: BoardStats): void {
  // Push into rolling buffer
  const smoke = stats.smoke ?? { pass: 0, fail: 0 };
  const total = smoke.pass + smoke.fail;
  const sample: BoardStatsSample = {
    ts:              Date.now(),
    hive_tokens:     stats.tokens?.hive   ?? 0,
    claude_tokens:   stats.tokens?.claude ?? 0,
    cost_usd:        stats.cost_usd       ?? 0,
    done_count:      stats.by_status?.['done'] ?? 0,
    smoke_pass_pct:  total > 0 ? (smoke.pass / total) * 100 : 0,
    parse_fail_rate: stats.parse_fail?.rate ?? 0,
  };
  _rollingBuf = pushSample(_rollingBuf, sample);
}

function onResize(_rect: Rect): void {
  // uPlot charts resize on next update cycle
}

function suspend(): void {
  _suspended = true;
}

function resume(): void {
  _suspended = false;
}

// ─── Plugin definition ────────────────────────────────────────────────────────

const telemetryPlugin: PanelPlugin = {
  id:          'telemetry',
  title:       'TELEMETRY',
  dataSources: [
    { kind: 'poll', endpoint: '/board/stats', intervalKey: 'board' },
  ],
  relevance,
  mount,
  update,
  onResize,
  suspend,
  resume,
};

register(telemetryPlugin);
export { telemetryPlugin };
