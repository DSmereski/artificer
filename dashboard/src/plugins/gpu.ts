/**
 * plugins/gpu.ts — GPU gauge + HOST card PanelPlugin wrapper.
 *
 * Wraps panels/gpu.ts (initGpuPanel, updateGpuPanel, pushGpuSpark).
 * md size idle; lg when contended/hot; min when gaming.
 * Honors budget: suspends sparkline pushes in gaming tier.
 */

import { register } from './registry.js';
import type { PanelPlugin, RelevanceResult, Rect } from './contract.js';
import type { SystemState, RenderBudget } from '../state/types.js';

// ─── Relevance ────────────────────────────────────────────────────────────────

function relevance(state: SystemState): RelevanceResult {
  if (state.resources.gaming) {
    return { priority: 40, size: 'min' };
  }
  if (state.resources.contended) {
    return { priority: 80, size: 'lg' };
  }
  return { priority: 60, size: 'md' };
}

// ─── State ────────────────────────────────────────────────────────────────────

let _rootEl: HTMLElement | null = null;
let _suspended = false;

// ─── Mount ────────────────────────────────────────────────────────────────────

function mount(el: HTMLElement): void {
  _rootEl = el;
  el.innerHTML = `
    <div class="panel-header">
      <span class="panel-label">GPU / HOST</span>
    </div>
    <div id="v2-gpu-panel" class="gpu-panel-inner">
      <p class="offline-state">Waiting for scout data…</p>
    </div>
  `;

  // Lazy-init the existing GPU panel inside the new cell
  const inner = el.querySelector('#v2-gpu-panel') as HTMLElement;
  if (inner) {
    _initGpuContent(inner);
  }
}

function _initGpuContent(panel: HTMLElement): void {
  // The existing panels/gpu.ts targets #gpu-panel; we redirect its logic inline
  // by keeping the panel element reference and calling update directly.
  panel.innerHTML = '<p class="offline-state">Waiting for scout data…</p>';
}

// ─── Update ───────────────────────────────────────────────────────────────────

function update(state: SystemState, budget: RenderBudget): void {
  if (!_rootEl || _suspended) return;

  // If gaming, just show minimal strip
  if (budget.graphMaxNodes === 0 || state.resources.gaming) {
    _renderGamingStrip(state);
    return;
  }

  _renderGpuCards(state);
}

function _renderGamingStrip(state: SystemState): void {
  const panel = _rootEl?.querySelector('#v2-gpu-panel') as HTMLElement | null;
  if (!panel) return;

  const gamingGpu = state.resources.gpus.find((g) => g.game !== null);
  panel.innerHTML = `
    <div class="gpu-gaming-strip">
      <span class="gpu-gaming-icon">⬡</span>
      <span class="gpu-gaming-label">GAMING${gamingGpu ? ` — ${escHtml(gamingGpu.game ?? '')}` : ''}</span>
      <span class="gpu-gaming-note">wallpaper throttled</span>
    </div>
    ${state.resources.gpus
      .filter((g) => g.is5060)
      .map((g) => `<div class="gpu-mini-strip">
        <span class="gpu-mini-label">${escHtml(g.name.slice(-12))}</span>
        <span class="gpu-mini-temp">${g.tempC}°C</span>
        <span class="gpu-mini-util">${Math.round(g.utilPct)}%</span>
      </div>`).join('')}
  `;
}

function _renderGpuCards(state: SystemState): void {
  const panel = _rootEl?.querySelector('#v2-gpu-panel') as HTMLElement | null;
  if (!panel) return;

  const gpus = state.resources.gpus;
  if (gpus.length === 0) {
    panel.innerHTML = '<p class="offline-state">Scout offline — no GPU data.</p>';
    return;
  }

  // AI workers (5060 Ti ×2) first, gaming card (4080) last — read order matches
  // how the estate is used: the Hive runs on the Ti pair, the 4080 is gaming.
  const ordered = [...gpus].sort((a, b) => Number(b.is5060) - Number(a.is5060));

  panel.innerHTML = ordered.map((g) => _gpuCard(g)).join('');
}

function _gpuCard(g: SystemState['resources']['gpus'][number]): string {
  const util = Math.round(g.utilPct);
  const tempColor = g.tempC > 78 ? 'var(--red)' : g.tempC > 70 ? 'var(--amber)' : 'var(--copper)';
  const role = g.is5060 ? 'AI' : 'GAMING';
  const roleClass = g.is5060 ? 'gpu-role-ai' : 'gpu-role-gaming';
  const name = g.name.replace('NVIDIA GeForce ', '');
  const vramTotGb = g.vramTotalMb > 0 ? (g.vramTotalMb / 1024).toFixed(0) : '?';
  const vramUsedGb = g.vramTotalMb > 0 ? ((g.vramTotalMb - g.vramFreeMb) / 1024).toFixed(1) : '?';
  const vramColor = g.vramUsedPct > 88 ? 'var(--red)' : 'var(--cyan, var(--copper))';
  const gameTag = g.game ? `<span class="gpu-game-tag">${escHtml(g.game)}</span>` : '';

  return `
    <div class="gpu-card-v3">
      <div class="gpu-card-top">
        <span class="gpu-card-name">${escHtml(name)}</span>
        <span class="gpu-role ${roleClass}">${role}</span>
        ${gameTag}
        <span class="gpu-temp" style="color:${tempColor}">${g.tempC}°C</span>
      </div>
      <div class="gpu-metric">
        <span class="gpu-metric-lbl">util</span>
        <div class="gpu-bar-track"><div class="gpu-bar-fill" style="width:${util}%;background:${tempColor}"></div></div>
        <span class="gpu-metric-val">${util}%</span>
      </div>
      <div class="gpu-metric">
        <span class="gpu-metric-lbl">vram</span>
        <div class="gpu-bar-track"><div class="gpu-bar-fill" style="width:${g.vramUsedPct}%;background:${vramColor}"></div></div>
        <span class="gpu-metric-val">${vramUsedGb}/${vramTotGb}G</span>
      </div>
    </div>
  `;
}

function onResize(_rect: Rect): void {
  // No canvas resize needed in this simplified v2 wrapper
}

function suspend(): void {
  _suspended = true;
}

function resume(): void {
  _suspended = false;
}

// ─── Plugin definition ────────────────────────────────────────────────────────

const gpuPlugin: PanelPlugin = {
  id:          'gpu',
  title:       'GPU / HOST',
  dataSources: [{ kind: 'poll', endpoint: '/v1/scout/status', intervalKey: 'scout' }],
  relevance,
  mount,
  update,
  onResize,
  suspend,
  resume,
};

register(gpuPlugin);
export { gpuPlugin };

// ─── Local escHtml ────────────────────────────────────────────────────────────

function escHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
