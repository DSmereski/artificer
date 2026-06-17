/**
 * plugins/clock.ts — Clock + uptime demo panel.
 *
 * A small new panel that proves the plugin scaffolding path:
 * - Shows live clock + gateway uptime
 * - sm size always (low priority, fills gaps)
 * - No external endpoint dependency (driven purely from SystemState)
 * - Serves as the Pv4 demo: "invoke skill → new panel appears"
 *
 * This file was hand-built to demonstrate the pattern; the dashboard-panel
 * skill scaffolds files with this exact shape from a template.
 */

import { register } from './registry.js';
import type { PanelPlugin, RelevanceResult } from './contract.js';
import type { SystemState, RenderBudget } from '../state/types.js';
import { fmtCost } from '../format.js';

// ─── Relevance ────────────────────────────────────────────────────────────────

function relevance(_state: SystemState): RelevanceResult {
  // Retired as a grid panel — the clock now lives in the top bar (it didn't
  // earn a whole cell for one number). Hidden, but kept registered so the
  // time logic stays available if a future layout wants it back.
  return { priority: 0, size: 'hidden' };
}

// ─── State ────────────────────────────────────────────────────────────────────

let _rootEl: HTMLElement | null = null;
let _clockInterval: ReturnType<typeof setInterval> | null = null;
let _suspended = false;

// ─── Mount ────────────────────────────────────────────────────────────────────

function mount(el: HTMLElement): void {
  _rootEl = el;
  el.innerHTML = `
    <div class="panel-header">
      <span class="panel-label">CLOCK</span>
    </div>
    <div class="clock-panel">
      <div class="clock-time" id="v2-clock-time">--:--:--</div>
      <div class="clock-date" id="v2-clock-date">---</div>
      <div class="clock-meta">
        <span class="clock-meta-row" id="v2-clock-status">⬡ offline</span>
        <span class="clock-meta-row" id="v2-clock-cost">cost: --</span>
        <span class="clock-meta-row" id="v2-clock-tier">tier: --</span>
      </div>
    </div>
  `;

  // Start live clock tick
  _startClock();
}

function _startClock(): void {
  if (_clockInterval !== null) clearInterval(_clockInterval);

  function tick(): void {
    if (_suspended) return;
    const now = new Date();
    const timeEl = document.getElementById('v2-clock-time');
    const dateEl = document.getElementById('v2-clock-date');
    if (timeEl) {
      timeEl.textContent = now.toLocaleTimeString('en-US', {
        hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit',
      });
    }
    if (dateEl) {
      dateEl.textContent = now.toLocaleDateString('en-US', {
        weekday: 'short', month: 'short', day: 'numeric',
      });
    }
  }

  tick();
  _clockInterval = setInterval(tick, 1_000);
}

// ─── Update ───────────────────────────────────────────────────────────────────

function update(state: SystemState, _budget: RenderBudget): void {
  if (!_rootEl || _suspended) return;

  const statusEl = document.getElementById('v2-clock-status');
  const costEl   = document.getElementById('v2-clock-cost');
  const tierEl   = document.getElementById('v2-clock-tier');

  if (statusEl) {
    statusEl.textContent = state.gatewayUp
      ? `⬡ ${state.activity}`
      : '⬡ offline';
    statusEl.style.color = state.gatewayUp ? 'var(--green)' : 'var(--red)';
  }

  if (costEl) {
    costEl.textContent = `cost: ${fmtCost(state.counts.costUsd)}`;
  }

  if (tierEl) {
    const tierColors: Record<string, string> = {
      idle:    'var(--green)',
      busy:    'var(--amber)',
      gaming:  'var(--red)',
      offline: 'var(--faint)',
    };
    tierEl.textContent  = `tier: ${state.tier}`;
    tierEl.style.color  = tierColors[state.tier] ?? 'var(--ink)';
  }
}

function suspend(): void {
  _suspended = true;
  if (_clockInterval !== null) {
    clearInterval(_clockInterval);
    _clockInterval = null;
  }
}

function resume(): void {
  _suspended = false;
  _startClock();
}

// ─── Plugin definition ────────────────────────────────────────────────────────

const clockPlugin: PanelPlugin = {
  id:          'clock',
  title:       'CLOCK',
  dataSources: [{ kind: 'state' }],
  relevance,
  mount,
  update,
  suspend,
  resume,
};

register(clockPlugin);
export { clockPlugin };
