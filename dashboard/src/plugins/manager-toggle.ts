/** Manager toggle panel — single icon button in a small panel slot.
 *
 * Calls POST /v1/crew/manager/toggle on click. Shows status dot:
 *   [green +] = ON  [gray o] = OFF
 */

import '@xterm/xterm/css/xterm.css';
import { register } from './registry.js';
import type { PanelPlugin, RelevanceResult } from './contract.js';
import type { SystemState, RenderBudget } from '../state/types.js';

let _enabled = false;
let _modelReady = false;
let _fetching = false;

// ── Status + toggle ──────────────────────────────────────────────

export async function fetchManagerStatus(): Promise<{
  enabled: boolean;
  model_ready: boolean;
  ollama_model: string;
  current_decision: string | null;
}> {
  try {
    const resp = await fetch('/v1/crew/manager/status');
    if (!resp.ok) return { enabled: false, model_ready: false, ollama_model: '', current_decision: null };
    return resp.json();
  } catch {
    return { enabled: false, model_ready: false, ollama_model: '', current_decision: null };
  }
}

export async function toggleManager(): Promise<void> {
  const newState = !_enabled;
  if (_fetching) return;
  _fetching = true;

  try {
    const resp = await fetch('/v1/crew/manager/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: newState }),
    });
    if (!resp.ok) throw new Error(`toggle failed: ${resp.status}`);
    const data = await resp.json();
    _enabled = !!data.enabled;
    renderToggle(_enabled, _modelReady);
  } catch (e) {
    console.error('[manager] toggle failed:', e);
  } finally {
    _fetching = false;
  }
}

// ── Panel render ─────────────────────────────────────────────────

function renderToggle(enabled: boolean, modelReady: boolean): void {
  const root = document.querySelector('#manager-toggle-panel');
  if (!root) return;

  const color = enabled ? 'var(--green)' : 'var(--dim)';
  const symbol = enabled ? '+' : 'o';
  const tooltip = modelReady
    ? (enabled
        ? 'Board Manager: ON (auto-decompose, assign, triage)'
        : 'Board Manager: OFF — click to enable')
    : 'Model unavailable — cannot toggle';

  const btn = root.querySelector('.manager-btn') as HTMLElement | null;
  if (!btn) return;
  btn.style.color = modelReady ? color : 'var(--faint)';
  btn.title = tooltip;
  (btn as HTMLButtonElement).disabled = !modelReady;
  btn.querySelector('.manager-symbol')!.textContent = symbol;
}

// ── Relevance ────────────────────────────────────────────────────

function relevance(state: SystemState): RelevanceResult {
  // Hidden when gaming, shown as small toggle panel otherwise
  return state.tier === 'gaming'
    ? { priority: 0, size: 'hidden' }
    : { priority: 12, size: 'sm' };
}

// ── Mount ────────────────────────────────────────────────────────

function mount(el: HTMLElement): void {
  el.innerHTML = `
    <div class="panel-header">
      <span class="panel-label">MANAGER</span>
    </div>
    <div id="manager-toggle-panel" style="display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;padding:12px;">
      <button type="button" class="manager-btn" style="width:64px;height:64px;border-radius:50%;border:2px solid var(--line);background:transparent;cursor:pointer;font-size:28px;color:var(--dim);font-family:var(--font-mono);transition:color 140ms ease, border-color 140ms ease;" title="Board Manager: o">
        <span class="manager-symbol">o</span>
      </button>
      <span style="font-size:10px;color:var(--dim);text-align:center;white-space:nowrap;">
        Board Manager
      </span>
    </div>
  `;

  const btn = el.querySelector('.manager-btn') as HTMLButtonElement | null;
  btn?.addEventListener('click', toggleManager);

  // Initial status
  fetchManagerStatus().then(s => {
    _enabled = !!s.enabled;
    _modelReady = !!s.model_ready;
    renderToggle(_enabled, _modelReady);
  }).catch(() => {});
}

// ── Update (periodic refresh) ────────────────────────────────────

function update(_state: SystemState, _budget: RenderBudget): void {
  // Refresh model readiness periodically
  fetchManagerStatus().then(s => {
    _modelReady = !!s.model_ready;
    renderToggle(_enabled, _modelReady);
  }).catch(() => {});
}

// ── Plugin definition ────────────────────────────────────────────

const managerPlugin: PanelPlugin = {
  id:          'manager-toggle',
  title:       'MANAGER',
  dataSources: [{ kind: 'state' }],
  relevance,
  mount,
  update,
};

register(managerPlugin);
export { managerPlugin };
