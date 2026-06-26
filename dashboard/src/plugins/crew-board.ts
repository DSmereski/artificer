/**
 * plugins/crew-board.ts — Crew board PanelPlugin wrapper.
 *
 * Wraps the existing board rendering logic from main.ts.
 * Hero size when building; lg (stats mode) when idle.
 * Self-registers on import.
 */

import { register } from './registry.js';
import type { PanelPlugin, RelevanceResult } from './contract.js';
import type { SystemState, RenderBudget } from '../state/types.js';
import { fmtNum, fmtCost, escHtml } from '../format.js';
import { activateFullBoard } from './crew-board-full.js';

// ─── F3: track progress for ring pulse ────────────────────────────────────────

let _prevProgress: number | null = null;

// ─── Relevance ────────────────────────────────────────────────────────────────

function relevance(state: SystemState): RelevanceResult {
  switch (state.activity) {
    case 'offline':
      return { priority: 20, size: 'sm' };
    case 'escalation':
      return { priority: 45, size: 'lg' };
    case 'building':
      return { priority: 90, size: 'hero' };
    case 'reviewing':
      return { priority: 75, size: 'lg' };
    case 'idle':
    default:
      return { priority: 50, size: 'lg' };
  }
}

// ─── State ────────────────────────────────────────────────────────────────────

let _rootEl: HTMLElement | null = null;
let _paused = false;

// ─── Mount ────────────────────────────────────────────────────────────────────

function mount(el: HTMLElement): void {
  _rootEl = el;
  el.innerHTML = `
    <div class="panel-header">
      <span class="panel-label">CREW BOARD</span>
      <button class="board-expand-btn" id="v2-board-expand" title="Open full board" aria-label="Open full board">⛶ full</button>
      <span class="board-state-chip" id="v2-board-chip">loading…</span>
    </div>
    <div id="v2-hero-card" class="hero-card">
      <div id="v2-hero-body" class="hero-body">
        <p class="hero-empty">Waiting for board data…</p>
      </div>
    </div>
    <div class="task-columns">
      <div class="task-col">
        <div class="task-col-header">READY <span id="v2-count-ready" class="task-count"></span></div>
        <div id="v2-list-ready" class="task-list"></div>
      </div>
      <div class="task-col">
        <div class="task-col-header">QA <span id="v2-count-qa" class="task-count"></span></div>
        <div id="v2-list-qa" class="task-list"></div>
      </div>
      <div class="task-col">
        <div class="task-col-header">REVIEW <span id="v2-count-review" class="task-count"></span></div>
        <div id="v2-list-review" class="task-list"></div>
      </div>
      <div class="task-col">
        <div class="task-col-header">DONE <span id="v2-count-done" class="task-count"></span></div>
        <div id="v2-list-done" class="task-list"></div>
      </div>
    </div>
    <div class="board-footer">
      <span class="board-stat" id="v2-stat-cost">cost: --</span>
      <span class="board-stat" id="v2-stat-tokens">tokens: --</span>
    </div>
  `;

  // Expand to the full embedded board: header button + clicking the hero card.
  el.querySelector('#v2-board-expand')?.addEventListener('click', (e) => {
    e.stopPropagation();
    activateFullBoard();
  });
  const hero = el.querySelector('#v2-hero-card') as HTMLElement | null;
  if (hero) {
    hero.style.cursor = 'pointer';
    hero.title = 'Open full board';
    hero.addEventListener('click', () => activateFullBoard());
  }
}

// ─── Update ───────────────────────────────────────────────────────────────────

function update(state: SystemState, _budget: RenderBudget): void {
  if (!_rootEl) return;

  _renderChip(state);
  _renderHero(state);
  _renderStats(state);
}

function _renderChip(state: SystemState): void {
  const chip = _rootEl?.querySelector('#v2-board-chip') as HTMLElement | null;
  if (!chip) return;

  const running = state.gatewayUp && state.activity !== 'offline';
  chip.textContent = !state.gatewayUp ? 'offline' :
                     _paused ? 'paused' : 'running';
  chip.className = `board-state-chip${running && !_paused ? ' running' : ' paused'}`;
}

function _renderHero(state: SystemState): void {
  const heroCard = _rootEl?.querySelector('#v2-hero-card') as HTMLElement | null;
  const heroBody = _rootEl?.querySelector('#v2-hero-body') as HTMLElement | null;
  if (!heroCard || !heroBody) return;

  if (!state.gatewayUp) {
    heroCard.classList.remove('building');
    heroBody.innerHTML = '<p class="hero-empty">Quiet. The swarm is offline.</p>';
    return;
  }

  if (state.tasks.building.length === 0) {
    heroCard.classList.remove('building');
    heroBody.innerHTML = '<p class="hero-empty">Quiet. Nothing building right now.</p>';
    return;
  }

  heroCard.classList.add('building');
  const task = state.tasks.building[0];

  const progressPct = Math.round(task.progress * 100);
  const stalledLabel = task.stalledMs > 300_000
    ? `<span class="hero-stalled">stalled ${Math.round(task.stalledMs / 60_000)}m</span>`
    : '';

  // F3: detect progress tick for ring pulse
  const progressChanged = _prevProgress !== null && _prevProgress !== task.progress;
  _prevProgress = task.progress;

  // Circular progress ring (SVG). r=16 → circumference ≈ 100.53.
  const C = 100.53;
  const off = (C * (1 - Math.min(1, Math.max(0, task.progress)))).toFixed(1);
  const ring = `
    <svg class="hero-ring" viewBox="0 0 40 40" aria-hidden="true">
      <circle class="hero-ring-bg" cx="20" cy="20" r="16"></circle>
      <circle class="hero-ring-fg" cx="20" cy="20" r="16"
              stroke-dasharray="${C}" stroke-dashoffset="${off}"></circle>
      <text class="hero-ring-txt" x="20" y="20" dy="0.34em" text-anchor="middle">${progressPct}</text>
    </svg>`;

  const nowDoing = task.lastAction
    ? `<div class="hero-now-doing"><span class="hero-now-dot"></span>${escHtml(task.lastAction)}</div>`
    : '';

  const hChip = task.hiveTokens ? `<span class="hero-tok tok-hive">H ${fmtNum(task.hiveTokens)}</span>` : '';
  const cChip = task.claudeTokens ? `<span class="hero-tok tok-claude">C ${fmtNum(task.claudeTokens)}</span>` : '';

  heroBody.innerHTML = `
    <div class="hero-grid">
      ${ring}
      <div class="hero-main">
        <div class="hero-title">${escHtml(task.title)}</div>
        <div class="hero-slug">${escHtml(task.slug)}${task.project ? ` · ${escHtml(task.project)}` : ''}</div>
        ${nowDoing}
        <div class="hero-meta">
          <span class="hero-stat">turns <span>${task.turns}</span></span>
          ${hChip}${cChip}
          ${stalledLabel}
          ${state.tasks.building.length > 1 ? `<span class="hero-extra">+${state.tasks.building.length - 1} more</span>` : ''}
        </div>
      </div>
    </div>
  `;

  // F3: fire ring glow pulse when progress just changed
  if (progressChanged) {
    const ringEl = heroBody.querySelector('.hero-ring') as SVGElement | null;
    if (ringEl) {
      ringEl.classList.remove('fx3-ring-pulse');
      // Force reflow so re-adding the class restarts the animation
      void ringEl.getBoundingClientRect();
      ringEl.classList.add('fx3-ring-pulse');
      ringEl.addEventListener('animationend', () => ringEl.classList.remove('fx3-ring-pulse'), { once: true });
    }
  }

  // Remaining columns (below hero)
  _renderTaskList('v2-count-ready',  state.tasks.ready);
  _renderTaskList('v2-count-qa',     state.tasks.qa);
  _renderTaskList('v2-count-review', state.tasks.review);
  _renderTaskList('v2-count-done',   state.tasks.done);
}

function _renderTaskList(countId: string, count: number): void {
  const countEl = _rootEl?.querySelector(`#${countId}`) as HTMLElement | null;
  if (countEl) countEl.textContent = count > 0 ? `(${count})` : '';
}

function _renderStats(state: SystemState): void {
  const costEl   = _rootEl?.querySelector('#v2-stat-cost') as HTMLElement | null;
  const tokensEl = _rootEl?.querySelector('#v2-stat-tokens') as HTMLElement | null;

  if (costEl)   costEl.textContent   = `cost: ${fmtCost(state.counts.costUsd)}`;
  if (tokensEl) tokensEl.textContent = `H:${fmtNum(state.counts.tokRateHive)} C:${fmtNum(state.counts.tokRateClaude)}`;
}

// ─── Plugin definition ────────────────────────────────────────────────────────

const crewBoardPlugin: PanelPlugin = {
  id:          'crew-board',
  title:       'CREW BOARD',
  dataSources: [
    { kind: 'poll', endpoint: '/board/state', intervalKey: 'board' },
    { kind: 'poll', endpoint: '/board/stats', intervalKey: 'board' },
    { kind: 'ws',   topic: 'board' },
  ],
  relevance,
  mount,
  update,
};

register(crewBoardPlugin);
export { crewBoardPlugin };
