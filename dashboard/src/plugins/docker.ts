/**
 * plugins/docker.ts — Docker containers panel (CC4 add-on).
 *
 * Lists local Docker containers (name, image, state, health) from the
 * gateway's loopback-exempt /v1/docker/status. Fed by a bridge from the
 * scout poll in main.ts. Hidden when Docker is unavailable AND nothing to
 * show; rises in priority if a container is exited/unhealthy.
 */

import { register } from './registry.js';
import type { PanelPlugin, RelevanceResult } from './contract.js';
import type { SystemState, RenderBudget } from '../state/types.js';
import type { DockerStatus } from '../types.js';
import { escHtml } from '../format.js';
import { resolveSettings } from './instances.js';

// ─── Settings ─────────────────────────────────────────────────────────────────

interface DockerSettings {
  /** Show exited/stopped containers. Default true == today's behavior. */
  showStopped: boolean;
}

const DEFAULT_SETTINGS: DockerSettings = { showStopped: true };

let _docker: DockerStatus | null = null;
let _rootEl: HTMLElement | null = null;

export function onDockerStatus(d: DockerStatus | null): void {
  _docker = d;
  _rerender();
}

// ─── Relevance ────────────────────────────────────────────────────────────────

function _anyTrouble(): boolean {
  const cs = _docker?.containers ?? [];
  return cs.some((c) => c.health === 'unhealthy' || (c.state !== 'running' && c.state !== 'exited'));
}

function relevance(state: SystemState): RelevanceResult {
  if (state.tier === 'gaming') return { priority: 0, size: 'hidden' };
  // Hide entirely if Docker isn't even installed (no signal to show).
  if (_docker && !_docker.available && (_docker.total ?? 0) === 0) {
    return { priority: 0, size: 'hidden' };
  }
  return { priority: _anyTrouble() ? 66 : 40, size: 'sm' };
}

// ─── Render ─────────────────────────────────────────────────────────────────────

function _stateClass(c: { state: string; health: string }): string {
  if (c.health === 'unhealthy') return 'dk-bad';
  if (c.state === 'running') return c.health === 'starting' ? 'dk-warn' : 'dk-up';
  if (c.state === 'exited') return 'dk-down';
  return 'dk-warn';
}

function mount(el: HTMLElement): void {
  _rootEl = el;
  el.innerHTML = `
    <div class="panel-header">
      <span class="panel-label">DOCKER</span>
      <span class="dk-count" id="dk-count"></span>
    </div>
    <div id="dk-body" class="dk-body"></div>
  `;
  _rerender();
}

function _rerender(): void {
  if (!_rootEl) return;
  const body = _rootEl.querySelector('#dk-body') as HTMLElement | null;
  const count = _rootEl.querySelector('#dk-count') as HTMLElement | null;
  if (!body) return;

  if (!_docker) {
    body.innerHTML = '<p class="offline-state">Waiting…</p>';
    if (count) count.textContent = '';
    return;
  }
  if (!_docker.available) {
    body.innerHTML = `<p class="offline-state">Docker ${escHtml(_docker.reason || 'unavailable')}.</p>`;
    if (count) count.textContent = '';
    return;
  }

  if (count) count.textContent = `${_docker.running}/${_docker.total}`;

  const { showStopped } = resolveSettings(_rootEl, 'docker', DEFAULT_SETTINGS);
  const visible = showStopped
    ? _docker.containers
    : _docker.containers.filter((c) => c.state !== 'exited');

  if (visible.length === 0) {
    body.innerHTML = showStopped
      ? '<p class="offline-state">No containers.</p>'
      : '<p class="offline-state">No running containers.</p>';
    return;
  }

  // running first, then by name
  const ordered = [...visible].sort((a, b) => {
    const ra = a.state === 'running' ? 0 : 1;
    const rb = b.state === 'running' ? 0 : 1;
    return ra - rb || a.name.localeCompare(b.name);
  });

  body.innerHTML = ordered.map((c) => {
    const stateClass = _stateClass(c);
    // F3: running dots get heartbeat pulse; unhealthy rows get hazard stripe
    const isRunning = stateClass === 'dk-up';
    const isUnhealthy = stateClass === 'dk-bad';
    const dotExtra = isRunning ? ' fx3-heartbeat' : '';
    const rowExtra = isUnhealthy ? ' fx3-hazard fx3-hazard-red' : '';
    return `
    <div class="dk-row${rowExtra}">
      <span class="dk-dot ${stateClass}${dotExtra}"></span>
      <div class="dk-info">
        <span class="dk-name">${escHtml(c.name)}</span>
        <span class="dk-sub">${escHtml(c.image)}</span>
      </div>
      <span class="dk-status">${escHtml(c.health || c.status.split(' ').slice(0, 2).join(' '))}</span>
    </div>
  `;
  }).join('');
}

function update(_state: SystemState, _budget: RenderBudget): void {
  // Driven by the docker bridge.
}

const dockerPlugin: PanelPlugin = {
  id:          'docker',
  title:       'DOCKER',
  dataSources: [{ kind: 'state' }],
  relevance,
  mount,
  update,
  defaultSettings: { ...DEFAULT_SETTINGS },
  settingsSchema: {
    fields: [
      {
        key: 'showStopped',
        label: 'Show stopped containers',
        type: 'boolean',
        default: true,
        hint: 'Include exited containers in the list',
      },
    ],
  },
};

register(dockerPlugin);
export { dockerPlugin };
