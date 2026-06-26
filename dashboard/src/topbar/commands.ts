/**
 * topbar/commands.ts — CC1 command surface.
 *
 * Turns the read-only dashboard into something you can ACT from: top-bar
 * Pause/Resume (dispatcher), + Task quick-add, ✦ Goal (decompose), and a
 * Ctrl+K command palette. All board mutations go through gateway.ts, which
 * fetches the loopback-only X-Board-Token once and caches it.
 *
 * Kept DOM-guarded so a node test import doesn't explode (no top-level DOM).
 */

import {
  pauseBoard,
  resumeBoard,
  createBoardTask,
  decomposeGoal,
  createContent,
  getBoardState,
} from '../gateway.js';
import { activateFullBoard } from '../plugins/crew-board-full.js';
import { all as allPanels, isPanelEnabled, setPanelEnabled } from '../plugins/registry.js';
import { logAction } from '../plugins/actions-log.js';

// ─── State ────────────────────────────────────────────────────────────────────

let _refresh: () => void = () => {};
let _relayout: () => void = () => {};
let _paused = false;
let _projects: string[] = [];

// ─── Command registry (palette) ───────────────────────────────────────────────

interface Command {
  id: string;
  label: string;
  hint?: string;
  run: () => void;
}

function _commands(): Command[] {
  return [
    {
      id: 'pause',
      label: _paused ? 'Resume dispatcher' : 'Pause dispatcher',
      hint: 'board',
      run: () => void _togglePause(),
    },
    { id: 'task',  label: 'New task…',         hint: 'create', run: () => _openModal('task') },
    { id: 'goal',  label: 'Decompose a goal…', hint: 'plan',   run: () => _openModal('goal') },
    { id: 'image', label: 'New image…',        hint: 'content', run: () => _openModal('image') },
    { id: 'video', label: 'New video…',        hint: 'content', run: () => _openModal('video') },
    { id: 'board', label: 'Open full crew board', hint: 'view', run: () => activateFullBoard() },
    { id: 'panels', label: 'Panels…', hint: 'config', run: () => _openPanels() },
    { id: 'refresh', label: 'Refresh board now', hint: 'poll',  run: () => _refresh() },
  ];
}

// ─── Panels manager (CC6 — enable/disable + persist) ──────────────────────────

function _openPanels(): void {
  _closePalette();
  const modal = $('panels-modal');
  const list = $('panels-list');
  if (!modal || !list) return;
  const rows = allPanels()
    .slice()
    .sort((a, b) => a.title.localeCompare(b.title))
    .map((p) => {
      const on = isPanelEnabled(p.id);
      return `<label class="panels-row">
        <input type="checkbox" class="panels-toggle" data-id="${p.id}" ${on ? 'checked' : ''} />
        <span class="panels-name">${escapeText(p.title)}</span>
        <span class="panels-id">${escapeText(p.id)}</span>
      </label>`;
    })
    .join('');
  list.innerHTML = rows;
  list.querySelectorAll<HTMLInputElement>('.panels-toggle').forEach((cb) => {
    cb.addEventListener('change', () => {
      const id = cb.dataset['id'];
      if (!id) return;
      setPanelEnabled(id, cb.checked);
      _relayout();
    });
  });
  modal.hidden = false;
}

function escapeText(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ─── DOM helpers ────────────────────────────────────────────────────────────────

function $<T extends HTMLElement>(id: string): T | null {
  return document.getElementById(id) as T | null;
}

// ─── Pause / resume ─────────────────────────────────────────────────────────────

async function _togglePause(): Promise<void> {
  const ok = _paused ? await resumeBoard() : await pauseBoard();
  if (ok) {
    _paused = !_paused;
    reflectBoardPaused(_paused);
    logAction('action', _paused ? 'Dispatcher paused' : 'Dispatcher resumed');
    _refresh();
  }
}

/** Update the Pause button to match the dispatcher's paused state. */
export function reflectBoardPaused(paused: boolean): void {
  _paused = paused;
  const btn = $('act-pause');
  if (!btn) return;
  btn.textContent = paused ? '▶ Resume' : '⏸ Pause';
  btn.classList.toggle('is-paused', paused);
}

/** True when the dispatcher is currently paused (for module action bars). */
export function isBoardPaused(): boolean { return _paused; }

// ─── #211: reusable command entry points for per-module action bars ───────────
// Module headers (crew-board, projects) call these so Pause/Task/Goal work the
// same whether you click the top bar or a module bar — same modal, same state.
export function cmdTogglePause(): void { void _togglePause(); }
export function cmdNewTask(): void { _openModal('task'); }
export function cmdNewGoal(): void { _openModal('goal'); }

// ─── Modal (task / goal) ────────────────────────────────────────────────────────

type ModalMode = 'task' | 'goal' | 'image' | 'video';
let _modalMode: ModalMode = 'task';
function _isContent(): boolean { return _modalMode === 'image' || _modalMode === 'video'; }

async function _populateProjects(): Promise<void> {
  const sel = $<HTMLSelectElement>('cmd-modal-project');
  if (!sel) return;
  if (_projects.length === 0) {
    try {
      const st = await getBoardState();
      _projects = (st.projects ?? [])
        .map((p) => p.slug)
        .filter((s): s is string => !!s)
        .sort();
    } catch {
      _projects = [];
    }
  }
  // Goal mode gets AUTO (classify → existing or new) + an explicit New option;
  // task mode must target a concrete existing project.
  const head =
    _modalMode === 'goal'
      ? [
          '<option value="auto">✨ Auto — pick the right project or create one</option>',
          '<option value="">+ New project (auto-named)</option>',
        ]
      : [];
  sel.innerHTML = head
    .concat(_projects.map((s) => `<option value="${s}">${s}</option>`))
    .join('');
}

function _openModal(mode: ModalMode): void {
  _modalMode = mode;
  _closePalette();
  const modal = $('cmd-modal');
  const title = $('cmd-modal-title');
  const text = $<HTMLInputElement>('cmd-modal-text');
  const submit = $('cmd-modal-submit');
  const msg = $('cmd-modal-msg');
  const sel = $<HTMLSelectElement>('cmd-modal-project');
  if (!modal || !text) return;
  const titles: Record<ModalMode, string> = {
    task: 'New task', goal: 'Decompose a goal',
    image: 'New image', video: 'New video',
  };
  const places: Record<ModalMode, string> = {
    task: 'Task title…', goal: 'High-level goal…',
    image: 'Describe the image…', video: 'Describe the video…',
  };
  const submits: Record<ModalMode, string> = {
    task: 'Create', goal: 'Decompose', image: 'Generate', video: 'Generate',
  };
  if (title) title.textContent = titles[mode];
  text.placeholder = places[mode];
  text.value = '';
  if (submit) submit.textContent = submits[mode];
  if (msg) msg.textContent = '';
  // Content requests don't need a project picker.
  if (sel) sel.style.display = _isContent() ? 'none' : '';
  modal.hidden = false;
  if (!_isContent()) void _populateProjects();
  text.focus();
}

function _closeModal(): void {
  const modal = $('cmd-modal');
  if (modal) modal.hidden = true;
}

async function _submitModal(): Promise<void> {
  const text = $<HTMLInputElement>('cmd-modal-text');
  const sel = $<HTMLSelectElement>('cmd-modal-project');
  const msg = $('cmd-modal-msg');
  if (!text) return;
  const value = text.value.trim();
  if (!value) { if (msg) msg.textContent = 'Enter some text first.'; return; }

  if (_isContent()) {
    if (msg) msg.textContent = 'Queuing…';
    const ok = await createContent({ type: _modalMode as 'image' | 'video', prompt: value });
    if (ok) {
      logAction('action', `${_modalMode === 'image' ? 'Image' : 'Video'} request: ${value}`);
      _closeModal();
      _refresh();
    } else if (msg) {
      msg.textContent = 'Failed — check the gateway / auth.';
    }
    return;
  }

  const project = sel?.value ?? '';
  // Tasks need a concrete project; goals accept 'auto' or '' (new project).
  if (_modalMode === 'task' && !project) {
    if (msg) msg.textContent = 'Pick a project.';
    return;
  }
  if (msg) msg.textContent = 'Working…';
  const projLabel = project || 'new';
  const ok =
    _modalMode === 'task'
      ? await createBoardTask({ title: value, project_slug: project })
      : await decomposeGoal({ goal: value, project_slug: project });
  if (ok) {
    logAction('action', _modalMode === 'task'
      ? `New task: ${value} (${project})`
      : `Decompose goal: ${value} (${projLabel})`);
    _closeModal();
    _refresh();
  } else if (msg) {
    msg.textContent = 'Failed — check the gateway / auth.';
  }
}

// ─── Command palette ────────────────────────────────────────────────────────────

let _sel = 0;

function _openPalette(): void {
  const pal = $('cmd-palette');
  const input = $<HTMLInputElement>('cmd-input');
  if (!pal || !input) return;
  pal.hidden = false;
  input.value = '';
  _sel = 0;
  _renderResults('');
  input.focus();
}

function _closePalette(): void {
  const pal = $('cmd-palette');
  if (pal) pal.hidden = true;
}

function _filtered(query: string): Command[] {
  const q = query.trim().toLowerCase();
  const cmds = _commands();
  if (!q) return cmds;
  return cmds.filter((c) => c.label.toLowerCase().includes(q));
}

function _renderResults(query: string): void {
  const box = $('cmd-results');
  if (!box) return;
  const cmds = _filtered(query);
  if (_sel >= cmds.length) _sel = Math.max(0, cmds.length - 1);
  box.innerHTML = cmds
    .map(
      (c, i) =>
        `<div class="cmd-row${i === _sel ? ' sel' : ''}" data-id="${c.id}">` +
        `<span>${c.label}</span>` +
        (c.hint ? `<span class="cmd-hint">${c.hint}</span>` : '') +
        `</div>`,
    )
    .join('');
  box.querySelectorAll<HTMLElement>('.cmd-row').forEach((row) => {
    row.addEventListener('click', () => {
      const id = row.dataset['id'];
      const cmd = _commands().find((c) => c.id === id);
      _closePalette();
      cmd?.run();
    });
  });
}

function _runSelected(query: string): void {
  const cmds = _filtered(query);
  const cmd = cmds[_sel];
  _closePalette();
  cmd?.run();
}

// ─── Init ─────────────────────────────────────────────────────────────────────

export function initCommandSurface(opts: { refresh: () => void; relayout?: () => void }): void {
  if (typeof document === 'undefined') return;
  _refresh = opts.refresh;
  if (opts.relayout) _relayout = opts.relayout;

  // Panels manager modal close (backdrop + ✕).
  const panelsModal = $('panels-modal');
  panelsModal?.addEventListener('click', (e) => {
    if (e.target === panelsModal) panelsModal.hidden = true;
  });
  $('panels-modal-close')?.addEventListener('click', () => {
    const m = $('panels-modal'); if (m) m.hidden = true;
  });

  $('act-pause')?.addEventListener('click', () => void _togglePause());
  $('act-task')?.addEventListener('click', () => _openModal('task'));
  $('act-goal')?.addEventListener('click', () => _openModal('goal'));
  $('act-palette')?.addEventListener('click', () => _openPalette());

  // Modal wiring
  $('cmd-modal-cancel')?.addEventListener('click', () => _closeModal());
  $('cmd-modal-submit')?.addEventListener('click', () => void _submitModal());
  $<HTMLInputElement>('cmd-modal-text')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') void _submitModal();
    if (e.key === 'Escape') _closeModal();
  });

  // Palette wiring
  const input = $<HTMLInputElement>('cmd-input');
  input?.addEventListener('input', () => { _sel = 0; _renderResults(input.value); });
  input?.addEventListener('keydown', (e) => {
    const cmds = _filtered(input.value);
    if (e.key === 'ArrowDown') { e.preventDefault(); _sel = Math.min(_sel + 1, cmds.length - 1); _renderResults(input.value); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); _sel = Math.max(_sel - 1, 0); _renderResults(input.value); }
    else if (e.key === 'Enter') { e.preventDefault(); _runSelected(input.value); }
    else if (e.key === 'Escape') { _closePalette(); }
  });

  // Global hotkey + overlay click-to-dismiss
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      _openPalette();
    }
  });
  $('cmd-palette')?.addEventListener('click', (e) => {
    if (e.target === $('cmd-palette')) _closePalette();
  });
  $('cmd-modal')?.addEventListener('click', (e) => {
    if (e.target === $('cmd-modal')) _closeModal();
  });
}
