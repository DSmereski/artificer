/**
 * plugins/git-activity.ts — Git activity feed (DASH it10).
 *
 * Recent commits across the crew projects (what the Hive has shipped), from the
 * loopback-exempt /v1/git/activity. Fed by a bridge from the 'right' poll.
 */

import { register } from './registry.js';
import type { PanelPlugin, RelevanceResult } from './contract.js';
import type { SystemState, RenderBudget } from '../state/types.js';
import type { GitActivity } from '../types.js';
import { escHtml, fmtRelative } from '../format.js';

let _data: GitActivity | null = null;
let _rootEl: HTMLElement | null = null;

export function onGitActivity(d: GitActivity | null): void {
  if (d) _data = d;
  _render();
}

function relevance(state: SystemState): RelevanceResult {
  if (state.tier === 'gaming') return { priority: 0, size: 'hidden' };
  return { priority: 38, size: 'sm' };
}

function mount(el: HTMLElement): void {
  _rootEl = el;
  el.innerHTML = `
    <div class="panel-header">
      <span class="panel-label">GIT ACTIVITY</span>
      <span class="git-count" id="git-count"></span>
    </div>
    <div id="git-list" class="git-list"></div>
  `;
  _render();
}

function _render(): void {
  if (!_rootEl) return;
  const list = _rootEl.querySelector('#git-list') as HTMLElement | null;
  const count = _rootEl.querySelector('#git-count') as HTMLElement | null;
  if (!list) return;

  const commits = _data?.commits ?? [];
  if (count) count.textContent = commits.length ? String(commits.length) : '';

  if (commits.length === 0) {
    list.innerHTML = '<p class="offline-state">No recent commits.</p>';
    return;
  }

  list.innerHTML = commits.map((c) => `
    <div class="git-row">
      <span class="git-hash">${escHtml(c.hash)}</span>
      <div class="git-info">
        <span class="git-subject">${escHtml(c.subject)}</span>
        <span class="git-sub">${escHtml(c.project)} · ${escHtml(fmtRelative(c.ts * 1000))}</span>
      </div>
    </div>
  `).join('');
}

function update(_state: SystemState, _budget: RenderBudget): void {
  // Driven by the git bridge.
}

const gitActivityPlugin: PanelPlugin = {
  id:          'git-activity',
  title:       'GIT ACTIVITY',
  dataSources: [{ kind: 'state' }],
  relevance,
  mount,
  update,
};

register(gitActivityPlugin);
export { gitActivityPlugin };
