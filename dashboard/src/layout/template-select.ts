/**
 * layout/template-select.ts — User-controlled layout template override.
 *
 * Lets the user pin the dashboard to a specific template (ultrawide / wide /
 * portrait) instead of letting pickTemplate() auto-select by viewport.
 * Selecting 'auto' restores the automatic behaviour (the default).
 *
 * Persistence: localStorage key 'dash:layoutTemplate'.
 * Pure module — DOM only touched by openTemplateSelector().
 */

import { pickTemplate, TEMPLATES } from './templates.js';
import type { Template, TemplateName } from './templates.js';
import { setDesktopMode, isDesktopActive } from './desktop.js';
import { openModuleManager } from '../plugins/module-manager.js';
import {
  getMonitorRole, setMonitorRole, isTopbarVisible, setTopbarVisible,
} from './monitor-config.js';
import {
  listLayouts, saveCurrentLayout, applyLayout, deleteLayout,
} from './named-layouts.js';

export type LayoutOverride = TemplateName | 'auto';

const LS_KEY = 'dash:layoutTemplate';
const _changeListeners: Array<() => void> = [];

// Per-monitor: the wallpaper host opens each monitor with ?win=<index>, so the
// template override is namespaced by it (legacy fallback on read so an existing
// global choice still applies on first run).
const WIN: string = (() => {
  try { return new URLSearchParams(location.search).get('win') ?? ''; }
  catch { return ''; }
})();
function _winKey(base: string): string { return WIN ? `${base}:${WIN}` : base; }

let _override: LayoutOverride = _loadOverride();

function _loadOverride(): LayoutOverride {
  try {
    if (typeof localStorage === 'undefined') return 'auto';
    const raw = localStorage.getItem(_winKey(LS_KEY)) ?? localStorage.getItem(LS_KEY);
    if (raw === 'ultrawide' || raw === 'wide' || raw === 'portrait') return raw;
    return 'auto';
  } catch {
    return 'auto';
  }
}

function _saveOverride(v: LayoutOverride): void {
  try {
    if (typeof localStorage === 'undefined') return;
    if (v === 'auto') localStorage.removeItem(_winKey(LS_KEY));
    else localStorage.setItem(_winKey(LS_KEY), v);
  } catch {}
}

/** Get the current override value ('auto' means use pickTemplate). */
export function getLayoutOverride(): LayoutOverride {
  return _override;
}

/** Set the layout override and notify listeners. */
export function setLayoutOverride(v: LayoutOverride): void {
  _override = v;
  _saveOverride(v);
  for (const cb of _changeListeners) cb();
}

/** Register a callback that fires when the override changes. */
export function onLayoutOverrideChange(cb: () => void): void {
  _changeListeners.push(cb);
}

/**
 * Resolve the template to use for the current viewport.
 * If an override is set, returns that template directly.
 * Otherwise delegates to pickTemplate(w, h).
 */
export function resolveTemplate(w: number, h: number): Template {
  if (_override !== 'auto') return TEMPLATES[_override];
  return pickTemplate(w, h);
}

// ─── Template selector overlay ───────────────────────────────────────────────

let _stylesInjected = false;

function _ensureStyles(): void {
  if (_stylesInjected || typeof document === 'undefined') return;
  _stylesInjected = true;
  const s = document.createElement('style');
  s.textContent = `
.tpl-backdrop {
  position: fixed; inset: 0; z-index: 9998;
  display: flex; align-items: flex-start; justify-content: flex-end;
  padding: 64px 16px 0;
  background: transparent;
}
.tpl-overlay {
  background: color-mix(in oklch, var(--panel, #18150f) 97%, transparent);
  border: 1px solid var(--line, #363c30);
  border-radius: 10px;
  box-shadow: 0 12px 36px oklch(0.05 0.01 60 / 0.5);
  padding: 14px;
  min-width: 200px;
  font-family: var(--font-mono, monospace);
  backdrop-filter: blur(10px);
}
.tpl-overlay h3 {
  font-size: 10px; letter-spacing: .12em; text-transform: uppercase;
  color: var(--dim, #666); margin: 0 0 10px;
}
.tpl-row {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 8px; border-radius: 6px; cursor: pointer;
  font-size: 12px; color: var(--ink, #ccc);
  border: 1px solid transparent;
  transition: background 80ms, border-color 80ms;
}
.tpl-row:hover { background: color-mix(in oklch, var(--amber, #c07840) 8%, transparent); border-color: var(--line, #363c30); }
.tpl-row.is-active { border-color: var(--amber, #c07840); color: var(--amber, #c07840); }
.tpl-row-badge {
  font-size: 9px; letter-spacing: .08em; color: var(--dim, #666);
  flex: 1; text-align: right;
}
.tpl-close-row { margin-top: 10px; text-align: right; }
.tpl-close-btn {
  font-family: inherit; font-size: 11px; font-weight: 600;
  color: var(--dim, #666); background: var(--card, #1a1a14);
  border: 1px solid var(--line, #363c30); border-radius: 5px;
  padding: 3px 10px; cursor: pointer;
}
.tpl-close-btn:hover { border-color: var(--amber, #c07840); color: var(--amber, #c07840); }
`;
  document.head.appendChild(s);
}

interface Choice {
  value: LayoutOverride;
  label: string;
  hint: string;
}

const CHOICES: Choice[] = [
  { value: 'auto',      label: 'Auto',      hint: 'by viewport' },
  { value: 'wide',      label: 'Wide',      hint: '4-col / 16:9' },
  { value: 'ultrawide', label: 'Ultrawide', hint: '8-col / 32:9' },
  { value: 'portrait',  label: 'Portrait',  hint: '1-col vertical' },
];

/**
 * Open the floating template selector overlay.
 * The onSelect callback is called with the chosen override value.
 */
export function openTemplateSelector(onSelect: (v: LayoutOverride) => void): void {
  if (typeof document === 'undefined') return;
  _ensureStyles();

  // Remove any existing overlay.
  document.querySelector('.tpl-backdrop')?.remove();

  const backdrop = document.createElement('div');
  backdrop.className = 'tpl-backdrop';

  const overlay = document.createElement('div');
  overlay.className = 'tpl-overlay';
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-label', 'Choose layout template');

  const h3 = document.createElement('h3');
  h3.textContent = '⊡ Layout';
  overlay.appendChild(h3);

  function _remove(): void { backdrop.remove(); }

  // Desktop (free-window) mode — drag/resize panels like a Windows desktop.
  // It's a distinct mode from the fixed templates below, so it gets its own row.
  const deskRow = document.createElement('div');
  deskRow.className = `tpl-row${isDesktopActive() ? ' is-active' : ''}`;
  deskRow.dataset['value'] = 'desktop';
  const deskLbl = document.createElement('span');
  deskLbl.textContent = 'Desktop';
  const deskBadge = document.createElement('span');
  deskBadge.className = 'tpl-row-badge';
  deskBadge.textContent = 'free windows';
  deskRow.appendChild(deskLbl);
  deskRow.appendChild(deskBadge);
  deskRow.addEventListener('click', () => {
    const wasDesktop = isDesktopActive();
    setDesktopMode(true);
    setLayoutOverride('auto');   // desktop overrides any template
    _remove();
    // Entering desktop is a full mode switch the live applier can't hot-swap —
    // reload so it initialises in desktop mode from a clean first render.
    if (!wasDesktop && typeof location !== 'undefined') location.reload();
    else onSelect('auto');
  });
  overlay.appendChild(deskRow);

  for (const choice of CHOICES) {
    const row = document.createElement('div');
    // A template is only "active" when desktop mode is OFF.
    const active = !isDesktopActive() && _override === choice.value;
    row.className = `tpl-row${active ? ' is-active' : ''}`;
    row.dataset['value'] = choice.value;

    const lbl = document.createElement('span');
    lbl.textContent = choice.label;

    const badge = document.createElement('span');
    badge.className = 'tpl-row-badge';
    badge.textContent = choice.hint;

    row.appendChild(lbl);
    row.appendChild(badge);
    row.addEventListener('click', () => {
      const wasDesktop = isDesktopActive();
      setDesktopMode(false);     // picking a template exits desktop mode
      setLayoutOverride(choice.value);
      _remove();
      // Leaving desktop mode is also a full switch → reload for a clean grid render.
      if (wasDesktop && typeof location !== 'undefined') location.reload();
      else onSelect(choice.value);
    });
    overlay.appendChild(row);
  }

  // #211: Modules + per-monitor config + saved layouts, folded into this one menu.
  _appendExtras(overlay, onSelect, _remove);

  const closeRow = document.createElement('div');
  closeRow.className = 'tpl-close-row';
  const closeBtn = document.createElement('button');
  closeBtn.className = 'tpl-close-btn';
  closeBtn.textContent = 'Close';
  closeBtn.addEventListener('click', _remove);
  closeRow.appendChild(closeBtn);
  overlay.appendChild(closeRow);

  backdrop.appendChild(overlay);
  document.body.appendChild(backdrop);

  // Click outside the overlay closes it.
  backdrop.addEventListener('click', (e) => {
    if (e.target === backdrop) _remove();
  });
}

// ─── #211: extra menu sections (Modules / This monitor / Saved layouts) ───────

function _heading(text: string): HTMLElement {
  const h = document.createElement('h3');
  h.textContent = text;
  h.style.marginTop = '12px';
  return h;
}

function _extraRow(label: string, badge: string, onClick: () => void): HTMLElement {
  const row = document.createElement('div');
  row.className = 'tpl-row';
  const lbl = document.createElement('span');
  lbl.textContent = label;
  const b = document.createElement('span');
  b.className = 'tpl-row-badge';
  b.textContent = badge;
  row.appendChild(lbl);
  row.appendChild(b);
  row.addEventListener('click', onClick);
  return row;
}

/**
 * Append the Modules, This-monitor and Saved-layouts sections to the Layout
 * overlay so it's the single place to arrange the dashboard (David: "layout and
 * modules should be the same button … a menu … save a customized layout …
 * select which monitor is primary/secondary and which has the top bar").
 */
function _appendExtras(
  overlay: HTMLElement,
  onSelect: (v: LayoutOverride) => void,
  remove: () => void,
): void {
  // — Modules — (the old standalone "Modules" button folds in here)
  overlay.appendChild(_heading('⊞ Modules'));
  overlay.appendChild(_extraRow('Manage modules…', 'add / remove', () => {
    remove();
    openModuleManager();
  }));

  // — This monitor — role + top-bar toggle (per ?win)
  overlay.appendChild(_heading('🖥 This monitor'));
  const roleRow = _extraRow('Role', getMonitorRole(), () => {
    const next = getMonitorRole() === 'primary' ? 'secondary' : 'primary';
    setMonitorRole(next);
    const badge = roleRow.querySelector('.tpl-row-badge');
    if (badge) badge.textContent = next;
  });
  overlay.appendChild(roleRow);
  const barRow = _extraRow('Top bar', isTopbarVisible() ? 'on' : 'off', () => {
    const next = !isTopbarVisible();
    setTopbarVisible(next);   // hides/shows #topbar immediately
    const badge = barRow.querySelector('.tpl-row-badge');
    if (badge) badge.textContent = next ? 'on' : 'off';
  });
  overlay.appendChild(barRow);

  // — Saved layouts — snapshot / restore / delete (per ?win)
  overlay.appendChild(_heading('💾 Saved layouts'));
  for (const name of listLayouts()) {
    const row = _extraRow(name, '✕', () => {
      if (applyLayout(name) && typeof location !== 'undefined') location.reload();
    });
    // The badge acts as a delete affordance for this layout.
    const del = row.querySelector('.tpl-row-badge') as HTMLElement | null;
    if (del) {
      del.style.cursor = 'pointer';
      del.title = 'Delete this layout';
      del.addEventListener('click', (e) => {
        e.stopPropagation();
        deleteLayout(name);
        remove();
        openTemplateSelector(onSelect);   // reopen so the list refreshes
      });
    }
    overlay.appendChild(row);
  }
  overlay.appendChild(_extraRow('＋ Save current layout…', 'name it', () => {
    const name = typeof prompt === 'function' ? prompt('Name this layout:') : null;
    if (!name || !name.trim()) return;
    saveCurrentLayout(name);
    remove();
    openTemplateSelector(onSelect);       // reopen so the new one appears
  }));
}

/** Reset override state (for unit tests only). */
export function _clearOverrideForTest(): void {
  _override = 'auto';
}
