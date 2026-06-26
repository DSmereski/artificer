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
import { getGpuMode, setGpuMode, type GpuMode } from '../gateway.js';

// ─── 4080 mode switch ("free the 4080") ───────────────────────────────────────
// auto -> force_on -> force_off -> auto. auto = AI uses the 4080 only when not
// gaming; force_on = always; force_off = the off switch (reserve for gaming).

const _MODE_CYCLE: GpuMode['mode'][] = ['auto', 'force_on', 'force_off'];
let _gpuMode: GpuMode['mode'] = 'auto';

function _modeLabel(m: GpuMode['mode']): string {
  return m === 'force_on' ? '4080:AI' : m === 'force_off' ? '4080:OFF' : 'AUTO';
}

function _paintSwitch(): void {
  const btn = _rootEl?.querySelector('#gpu-mode-switch') as HTMLElement | null;
  if (btn) {
    btn.textContent = _modeLabel(_gpuMode);
    btn.dataset.mode = _gpuMode;
  }
}

async function _cycleMode(): Promise<void> {
  const next = _MODE_CYCLE[(_MODE_CYCLE.indexOf(_gpuMode) + 1) % _MODE_CYCLE.length];
  _gpuMode = next;
  _paintSwitch();                       // optimistic
  const res = await setGpuMode(next);
  if (res) {
    _gpuMode = res.mode;
    _paintSwitch();
  }
}

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
      <button id="gpu-mode-switch" class="gpu-mode-switch" data-mode="auto"
        title="4080 AI policy — click to cycle: AUTO (use when not gaming) / 4080:AI (always) / 4080:OFF (reserve for gaming)"
        style="margin-left:auto;font:inherit;font-size:10px;letter-spacing:.5px;cursor:pointer;background:rgba(255,255,255,.06);color:var(--copper,#c87);border:1px solid rgba(255,255,255,.15);border-radius:4px;padding:1px 6px;">AUTO</button>
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

  // Wire the 4080 switch + load its current state.
  const sw = el.querySelector('#gpu-mode-switch') as HTMLElement | null;
  sw?.addEventListener('click', (e) => {
    e.stopPropagation();          // don't trigger panel focus-mode
    void _cycleMode();
  });
  void getGpuMode().then((s) => {
    if (s) { _gpuMode = s.mode; _paintSwitch(); }
  });
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
  // F3: temp glow — fx3-temp-hot at >78°C (breathing red), fx3-temp-warm at >70°C
  const tempGlowClass = g.tempC > 78 ? ' fx3-temp-hot' : g.tempC > 70 ? ' fx3-temp-warm' : '';

  return `
    <div class="gpu-card-v3">
      <div class="gpu-card-top">
        <span class="gpu-card-name">${escHtml(name)}</span>
        <span class="gpu-role ${roleClass}">${role}</span>
        ${gameTag}
        <span class="gpu-temp${tempGlowClass}" style="color:${tempColor}">${g.tempC}°C</span>
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
      ${_gpuProcRow(g.processes)}
    </div>
  `;
}

// Max process chips per card before collapsing into a "+N more" tail.
const _MAX_PROC_CHIPS = 4;

/** Render the "what's running on this card" row. Empty cards show "idle". */
function _gpuProcRow(
  procs: SystemState['resources']['gpus'][number]['processes'],
): string {
  if (!procs || procs.length === 0) {
    return `<div class="gpu-procs gpu-procs-idle"><span class="gpu-proc-empty">idle</span></div>`;
  }
  // Server pre-sorts heaviest VRAM first.
  const shown = procs.slice(0, _MAX_PROC_CHIPS);
  const overflow = procs.length - shown.length;
  const chips = shown.map((p) => {
    const gb = p.usedMemoryMb >= 1024
      ? `${(p.usedMemoryMb / 1024).toFixed(1)}G`
      : `${p.usedMemoryMb}M`;
    const mem = p.usedMemoryMb > 0 ? ` <span class="gpu-proc-mem">${gb}</span>` : '';
    return `<span class="gpu-proc-chip" title="pid ${p.pid}">${escHtml(p.name)}${mem}</span>`;
  });
  const more = overflow > 0
    ? `<span class="gpu-proc-chip gpu-proc-more">+${overflow}</span>`
    : '';
  return `<div class="gpu-procs">${chips.join('')}${more}</div>`;
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
