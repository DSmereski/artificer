/**
 * plugins/system.ts — System + Services panel (CC4).
 *
 * Service-health dots (gateway / Hive bot / Scout / vault-writer up-down +
 * uptime) and a host strip (CPU %, RAM %, disk usage) — all from the scout
 * /v1/scout/status payload that the scout poll already fetches. Fed by a
 * bridge from main.ts (mirrors the suno / needs-you pattern); no new endpoint.
 */

import { register } from './registry.js';
import type { PanelPlugin, RelevanceResult } from './contract.js';
import type { SystemState, RenderBudget } from '../state/types.js';
import type { ScoutStatus } from '../types.js';
import { escHtml } from '../format.js';

// ─── Cached scout payload ─────────────────────────────────────────────────────

let _scout: ScoutStatus | null = null;
let _rootEl: HTMLElement | null = null;

export function onSystemScout(scout: ScoutStatus | null): void {
  if (scout) _scout = scout;
  _rerender();
}

// ─── Relevance ────────────────────────────────────────────────────────────────

function relevance(state: SystemState): RelevanceResult {
  if (state.tier === 'gaming') return { priority: 0, size: 'hidden' };
  // Always useful, modest footprint; rises a little if a service is down.
  const anyDown = (_scout?.bots ?? []).some((b) => !b.is_running);
  return { priority: anyDown ? 70 : 42, size: 'md' };
}

// ─── Helpers ────────────────────────────────────────────────────────────────────

function _fmtUptime(s: number | undefined): string {
  if (!s || s <= 0) return '';
  if (s < 90) return `${Math.round(s)}s`;
  if (s < 5400) return `${Math.round(s / 60)}m`;
  if (s < 172800) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

function _bar(label: string, pct: number, detail: string): string {
  const p = Math.max(0, Math.min(100, Math.round(pct)));
  const color = p > 90 ? 'var(--red)' : p > 75 ? 'var(--amber)' : 'var(--copper)';
  return `
    <div class="sys-metric">
      <span class="sys-metric-lbl">${escHtml(label)}</span>
      <div class="gpu-bar-track"><div class="gpu-bar-fill" style="width:${p}%;background:${color}"></div></div>
      <span class="sys-metric-val">${escHtml(detail)}</span>
    </div>`;
}

// ─── Mount / render ─────────────────────────────────────────────────────────────

function mount(el: HTMLElement): void {
  _rootEl = el;
  el.innerHTML = `
    <div class="panel-header"><span class="panel-label">SYSTEM / SERVICES</span></div>
    <div id="sys-body" class="sys-body"></div>
  `;
  _rerender();
}

function _rerender(): void {
  if (!_rootEl) return;
  const body = _rootEl.querySelector('#sys-body') as HTMLElement | null;
  if (!body) return;

  if (!_scout) {
    body.innerHTML = '<p class="offline-state">Waiting for scout…</p>';
    return;
  }

  const bots = _scout.bots ?? [];
  const dots = bots.map((b) => `
    <div class="svc-dot-row">
      <span class="svc-dot ${b.is_running ? 'svc-up' : 'svc-down'}"></span>
      <span class="svc-name">${escHtml(b.name)}</span>
      <span class="svc-uptime">${b.is_running ? escHtml(_fmtUptime(b.uptime_seconds)) : 'down'}</span>
    </div>`).join('');

  const host = _scout.host;
  const cpu = host?.cpu?.usage_pct ?? 0;
  const ram = host?.ram?.used_pct ?? 0;
  const ramDetail = host?.ram
    ? `${(host.ram.used_gb ?? 0).toFixed(0)}/${(host.ram.total_gb ?? 0).toFixed(0)}G`
    : '';
  const sysBars =
    _bar('cpu', cpu, `${Math.round(cpu)}%`) +
    _bar('ram', ram, ramDetail) +
    (_scout.disks ?? [])
      .slice(0, 3)
      .map((d) => _bar(d.drive.replace(/\\$/, ''), d.used_pct, `${d.free_gb.toFixed(0)}G free`))
      .join('');

  body.innerHTML = `
    <div class="svc-dots">${dots || '<span class="offline-state">no services</span>'}</div>
    <div class="sys-bars">${sysBars}</div>
  `;
}

function update(_state: SystemState, _budget: RenderBudget): void {
  // Driven by the scout bridge; nothing per-tick.
}

// ─── Plugin definition ────────────────────────────────────────────────────────

const systemPlugin: PanelPlugin = {
  id:          'system',
  title:       'SYSTEM / SERVICES',
  dataSources: [{ kind: 'state' }],
  relevance,
  mount,
  update,
};

register(systemPlugin);
export { systemPlugin };
