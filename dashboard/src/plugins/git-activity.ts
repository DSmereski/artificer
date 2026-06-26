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
// F3: track seen hashes so we can flash new arrivals
const _seenHashes = new Set<string>();

export function onGitActivity(d: GitActivity | null): void {
  if (d) {
    // Identify newly-arrived commits before updating _data
    _newHashes = new Set(
      (d.commits ?? []).map((c) => c.hash).filter((h) => !_seenHashes.has(h))
    );
    _data = d;
    for (const c of d.commits ?? []) _seenHashes.add(c.hash);
  }
  _render();
}

let _newHashes = new Set<string>();

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

  // F3: new commits get the fresh-flash class; apply post-render via rAF
  const newSet = _newHashes;
  _newHashes = new Set<string>();

  list.innerHTML = commits.map((c) => {
    // F3: fx3-matrix = green monospace glow on hash; fx3-fresh = single-shot flash on new rows
    const freshClass = newSet.has(c.hash) ? ' fx3-fresh' : '';
    return `
    <div class="git-row${freshClass}">
      <span class="git-hash fx3-matrix">${escHtml(c.hash.slice(0, 7))}</span>
      <div class="git-info">
        <span class="git-subject">${escHtml(c.subject)}</span>
        <span class="git-sub">${escHtml(c.project)} · ${escHtml(fmtRelative(c.ts * 1000))}</span>
      </div>
    </div>
  `;
  }).join('');

  // Remove the fx3-fresh class after the animation ends (self-cleaning)
  if (newSet.size > 0) {
    list.querySelectorAll<HTMLElement>('.fx3-fresh').forEach((row) => {
      row.addEventListener('animationend', () => row.classList.remove('fx3-fresh'), { once: true });
    });
  }
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
