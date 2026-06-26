/**
 * topbar/board-switcher.ts — Project filter selector for the top bar.
 *
 * Fetches /board/state to read the project list, renders a compact <select>.
 * Selecting a project sets a client-side filter so the crew board only shows
 * tasks for that project. "All" clears the filter (back-compat / default).
 *
 * Persists the active project to localStorage key 'dash:activeProject'.
 * Exports getActiveBoard()/onBoardChange() so main.ts needs no changes.
 */
import { getBoardState } from '../gateway.js';

const LS_ACTIVE_PROJECT = 'dash:activeProject';
let _activeProject: string | null = null;
const _listeners: Array<(p: string | null) => void> = [];

function _load(): void {
  try {
    _activeProject = typeof localStorage !== 'undefined'
      ? localStorage.getItem(LS_ACTIVE_PROJECT)
      : null;
  } catch { _activeProject = null; }
}

function _save(p: string | null): void {
  try {
    if (typeof localStorage === 'undefined') return;
    if (p === null) localStorage.removeItem(LS_ACTIVE_PROJECT);
    else localStorage.setItem(LS_ACTIVE_PROJECT, p);
  } catch {}
}

function _notify(): void {
  for (const cb of _listeners) cb(_activeProject);
}

/** Returns the currently selected project slug, or null for "All". */
export function getActiveBoard(): string | null { return _activeProject; }

/** Register a callback that fires whenever the project selection changes. */
export function onBoardChange(cb: (p: string | null) => void): void {
  _listeners.push(cb);
}

let _stylesInjected = false;

function _ensureStyles(): void {
  if (_stylesInjected || typeof document === 'undefined') return;
  _stylesInjected = true;
  const style = document.createElement('style');
  style.textContent = `
.board-switcher {
  display: inline-flex; align-items: center; gap: 6px;
  font-family: var(--font-mono, monospace);
}
.board-switcher-label {
  font-size: 10px; letter-spacing: .1em; text-transform: uppercase;
  color: var(--dim, #666);
}
.board-switcher-select {
  background: var(--panel, #14110f);
  color: var(--fg, #ccc);
  border: 1px solid var(--line, #363c30);
  border-radius: 5px;
  font-family: var(--font-mono, monospace);
  font-size: 11px;
  padding: 2px 6px;
  cursor: pointer;
}
.board-switcher-select:hover { border-color: var(--amber, #c07840); }
`;
  document.head.appendChild(style);
}

export async function initBoardSwitcher(container: HTMLElement): Promise<void> {
  _load();
  _ensureStyles();

  // Idempotent: drop any prior switcher on re-render.
  container.querySelector('.board-switcher')?.remove();

  let projects: Array<{ slug: string }> = [];
  try {
    const state = await getBoardState();
    projects = state.projects ?? [];
  } catch {}

  const wrap = document.createElement('div');
  wrap.className = 'board-switcher';
  wrap.innerHTML = `<label class="board-switcher-label">Project</label>`;

  const sel = document.createElement('select');
  sel.className = 'board-switcher-select';
  sel.setAttribute('aria-label', 'Filter by project');

  const allOpt = document.createElement('option');
  allOpt.value = '';
  allOpt.textContent = 'All';
  sel.appendChild(allOpt);

  for (const p of projects) {
    const opt = document.createElement('option');
    opt.value = p.slug;
    opt.textContent = p.slug;
    if (p.slug === _activeProject) opt.selected = true;
    sel.appendChild(opt);
  }

  if (_activeProject === null) (sel.options[0] as HTMLOptionElement).selected = true;

  sel.addEventListener('change', () => {
    _activeProject = sel.value || null;
    _save(_activeProject);
    _notify();
  });

  wrap.appendChild(sel);
  container.appendChild(wrap);
}
