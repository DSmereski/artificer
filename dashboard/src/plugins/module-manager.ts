/**
 * plugins/module-manager.ts — Module Manager UI (P0, v-Next Spine A).
 *
 * Provides two surfaces:
 *
 * 1. Module Manager menu  (`openModuleManager` / `ModuleManagerOverlay`)
 *    A floating overlay listing all registered plugin types. From it the user can:
 *    - Add a new default instance of any type.
 *    - Add a duplicate of an existing instance (same type, second copy).
 *    - Remove an existing instance.
 *    When no instances are configured yet the overlay bootstraps the first one
 *    (activating the instance layer for the first time).
 *
 * 2. Per-instance settings gear (`attachInstanceGear`)
 *    Appended to a panel's header in focus mode. Clicking the gear opens a
 *    schema-driven settings form for that specific instance.
 *
 * Design notes:
 *   - Mouse-only interactions (Lively forwards mouse, not keyboard).
 *   - Back-compat: if NO instances are stored the overlay works in "adopt
 *     current plugins" mode — clicking Add on a type creates its first instance
 *     without breaking the existing layout.
 *   - Deliberately minimal styling (that's a later phase); functional first.
 *   - No external deps; plain DOM + the existing CSS variable palette.
 */

import { all as allPlugins, get as getPlugin, isPanelEnabled, setPanelEnabled } from './registry.js';
import { isDesktopActive, bringPanelToFront } from '../layout/desktop.js';
import { isModuleEnabledForLayout, setModuleEnabledForLayout } from '../layout/layout-modules.js';
import { getLayoutOverride } from '../layout/template-select.js';
import type { PanelPlugin } from './contract.js';
import {
  updateInstanceSettings,
  getInstance,
} from './instances.js';
import type { ModuleInstance } from './instances.js';
import { exportWorkspace, importWorkspace } from '../layout/config-io.js';

// ─── Re-render callback ────────────────────────────────────────────────────────

/**
 * A callback registered by main.ts so the manager can trigger a store re-emit
 * (i.e. a layout refresh) after add/remove operations.
 */
let _onChangeCallback: (() => void) | null = null;

export function setModuleManagerChangeCallback(fn: () => void): void {
  _onChangeCallback = fn;
}

function _notifyChange(): void {
  _onChangeCallback?.();
}

// ─── Overlay styles (injected once) ───────────────────────────────────────────

let _stylesInjected = false;

function _ensureStyles(): void {
  if (_stylesInjected) return;
  _stylesInjected = true;
  const style = document.createElement('style');
  style.textContent = `
/* Module Manager overlay */
.mm-backdrop {
  position: fixed; inset: 0;
  background: rgba(0,0,0,.55);
  display: flex; align-items: center; justify-content: center;
  z-index: 9999;
}
.mm-overlay {
  background: var(--bg, #111);
  border: 1px solid var(--line, #333);
  border-radius: 8px;
  padding: 20px 24px;
  min-width: 360px;
  max-width: 520px;
  max-height: 80vh;
  overflow-y: auto;
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  color: var(--fg, #ccc);
  box-shadow: 0 8px 32px rgba(0,0,0,.6);
}
.mm-overlay h2 {
  margin: 0 0 14px;
  font-size: 13px;
  letter-spacing: .12em;
  color: var(--amber, #c07840);
  text-transform: uppercase;
}
.mm-section-label {
  font-size: 10px;
  letter-spacing: .1em;
  color: var(--dim, #666);
  text-transform: uppercase;
  margin: 14px 0 6px;
}
.mm-row {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid var(--line, #222);
}
.mm-row:last-child { border-bottom: none; }
.mm-row-label { flex: 1; color: var(--fg, #ccc); font-size: 13px; }
.mm-row-sub { font-size: 10px; color: var(--dim, #666); margin-left: 4px; }
.mm-btn {
  background: transparent;
  border: 1px solid var(--line, #444);
  border-radius: 5px;
  color: var(--fg, #ccc);
  cursor: pointer;
  font-family: var(--font-mono, monospace);
  font-size: 13px;
  /* Fat target — Lively forwards the mouse but small buttons are hard to hit. */
  padding: 8px 16px;
  min-width: 76px;
  text-align: center;
  white-space: nowrap;
}
.mm-btn:hover { border-color: var(--amber, #c07840); color: var(--amber, #c07840); }
.mm-btn.mm-btn-remove { color: var(--red, #c04040); }
.mm-btn.mm-btn-remove:hover { border-color: var(--red, #c04040); }
.mm-close-row {
  display: flex; justify-content: flex-end;
  margin-top: 16px;
}

/* Settings form */
.mm-settings-form {
  display: flex; flex-direction: column; gap: 10px;
  margin-top: 10px;
}
.mm-field { display: flex; flex-direction: column; gap: 3px; }
.mm-field label { font-size: 11px; color: var(--dim, #888); }
.mm-field input, .mm-field select {
  background: var(--bg, #111);
  border: 1px solid var(--line, #444);
  border-radius: 4px;
  color: var(--fg, #ccc);
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  padding: 4px 8px;
}
.mm-field input[type="checkbox"] { width: auto; align-self: flex-start; }
.mm-field-hint { font-size: 10px; color: var(--faint, #444); }

/* Instance gear button (focus-mode corner) */
.inst-gear-btn {
  background: transparent;
  border: none;
  color: var(--dim, #666);
  cursor: pointer;
  font-size: 14px;
  padding: 2px 6px;
  line-height: 1;
  border-radius: 3px;
}
.inst-gear-btn:hover { color: var(--amber, #c07840); }

/* Export / Import section — visually separated from the module list */
.mm-io {
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid var(--line, #333);
}
.mm-io-row { display: flex; gap: 8px; margin-top: 6px; }
.mm-io-textarea {
  width: 100%;
  min-height: 92px;
  resize: vertical;
  box-sizing: border-box;
  margin-top: 8px;
  background: var(--bg, #111);
  border: 1px solid var(--line, #444);
  border-radius: 4px;
  color: var(--fg, #ccc);
  font-family: var(--font-mono, monospace);
  font-size: 11px;
  padding: 6px 8px;
  white-space: pre;
  overflow: auto;
}
.mm-io-status { font-size: 10px; margin-top: 6px; min-height: 12px; }
.mm-io-status.is-ok { color: var(--green, #5cc870); }
.mm-io-status.is-err { color: var(--red, #c04040); }
`;
  document.head.appendChild(style);
}

// ─── Settings form builder ─────────────────────────────────────────────────────

function _buildSettingsForm(plugin: PanelPlugin, instance: ModuleInstance): HTMLElement {
  const form = document.createElement('div');
  form.className = 'mm-settings-form';

  const schema = plugin.settingsSchema;
  if (!schema || schema.fields.length === 0) {
    const note = document.createElement('p');
    note.style.cssText = 'margin:4px 0;font-size:11px;color:var(--dim,#666);';
    note.textContent = 'No configurable settings for this module.';
    form.appendChild(note);
    return form;
  }

  for (const field of schema.fields) {
    const wrapper = document.createElement('div');
    wrapper.className = 'mm-field';

    const lbl = document.createElement('label');
    lbl.textContent = field.label;
    wrapper.appendChild(lbl);

    const currentValue =
      instance.settings[field.key] !== undefined
        ? instance.settings[field.key]
        : field.default;

    let input: HTMLInputElement | HTMLSelectElement;

    if (field.type === 'boolean') {
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = !!currentValue;
      cb.dataset['fieldKey'] = field.key;
      cb.dataset['fieldType'] = 'boolean';
      input = cb;
    } else if (field.type === 'select' && field.options) {
      const sel = document.createElement('select');
      sel.dataset['fieldKey'] = field.key;
      sel.dataset['fieldType'] = 'select';
      for (const opt of field.options) {
        const o = document.createElement('option');
        o.value = opt.value;
        o.textContent = opt.label;
        if (String(currentValue) === opt.value) o.selected = true;
        sel.appendChild(o);
      }
      input = sel;
    } else if (field.type === 'number') {
      const inp = document.createElement('input');
      inp.type = 'number';
      inp.value = String(currentValue);
      inp.dataset['fieldKey'] = field.key;
      inp.dataset['fieldType'] = 'number';
      input = inp;
    } else {
      const inp = document.createElement('input');
      inp.type = 'text';
      inp.value = String(currentValue);
      inp.dataset['fieldKey'] = field.key;
      inp.dataset['fieldType'] = 'string';
      input = inp;
    }

    wrapper.appendChild(input);

    if (field.hint) {
      const hint = document.createElement('span');
      hint.className = 'mm-field-hint';
      hint.textContent = field.hint;
      wrapper.appendChild(hint);
    }

    form.appendChild(wrapper);
  }

  return form;
}

function _readSettingsForm(
  form: HTMLElement,
  schema: PanelPlugin['settingsSchema'],
): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  if (!schema) return result;

  for (const field of schema.fields) {
    const el = form.querySelector<HTMLElement>(`[data-field-key="${field.key}"]`);
    if (!el) continue;
    const type = (el as HTMLElement).dataset['fieldType'] ?? field.type;
    if (type === 'boolean') {
      result[field.key] = (el as HTMLInputElement).checked;
    } else if (type === 'number') {
      const raw = (el as HTMLInputElement).value;
      result[field.key] = raw === '' ? field.default : Number(raw);
    } else {
      result[field.key] = (el as HTMLInputElement | HTMLSelectElement).value;
    }
  }

  return result;
}

// ─── Per-instance settings dialog ────────────────────────────────────────────

function _openSettingsDialog(instanceId: string): void {
  _ensureStyles();

  const instance = getInstance(instanceId);
  if (!instance) return;
  const plugin = getPlugin(instance.type);
  if (!plugin) return;

  // Build backdrop + overlay
  const backdrop = document.createElement('div');
  backdrop.className = 'mm-backdrop';

  const overlay = document.createElement('div');
  overlay.className = 'mm-overlay';
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-label', `Settings for ${plugin.title}`);

  const h2 = document.createElement('h2');
  h2.textContent = `${plugin.title} settings`;
  overlay.appendChild(h2);

  const instanceNote = document.createElement('div');
  instanceNote.style.cssText = 'font-size:10px;color:var(--dim,#666);margin-bottom:10px;';
  instanceNote.textContent = `instance: ${instance.instanceId}`;
  overlay.appendChild(instanceNote);

  const form = _buildSettingsForm(plugin, instance);
  overlay.appendChild(form);

  // Save / Cancel
  const saveRow = document.createElement('div');
  saveRow.className = 'mm-close-row';
  saveRow.style.gap = '8px';

  const saveBtn = document.createElement('button');
  saveBtn.type = 'button';
  saveBtn.className = 'mm-btn';
  saveBtn.textContent = 'Save';

  const cancelBtn = document.createElement('button');
  cancelBtn.type = 'button';
  cancelBtn.className = 'mm-btn';
  cancelBtn.textContent = 'Cancel';

  saveRow.appendChild(cancelBtn);
  saveRow.appendChild(saveBtn);
  overlay.appendChild(saveRow);

  backdrop.appendChild(overlay);
  document.body.appendChild(backdrop);

  const _close = (): void => {
    backdrop.remove();
  };

  cancelBtn.addEventListener('click', _close);
  backdrop.addEventListener('click', (e) => {
    if (e.target === backdrop) _close();
  });

  saveBtn.addEventListener('click', () => {
    const patch = _readSettingsForm(form, plugin.settingsSchema);
    updateInstanceSettings(instanceId, patch);
    _notifyChange();
    _close();
  });
}

// ─── Gear button (focus-mode corner) ─────────────────────────────────────────

/**
 * Attach a gear button to an instance's panel header element.
 * Called by the layout/mount path when an instance's cell is created.
 * The gear is only visually prominent when the page is in focus mode
 * (CSS can hide it in ambient mode if desired).
 *
 * @param headerEl  The `.panel-header` element to append the gear to.
 * @param instanceId  The instance this gear controls.
 */
export function attachInstanceGear(headerEl: HTMLElement, instanceId: string): void {
  _ensureStyles();

  // Don't double-attach
  if (headerEl.querySelector('.inst-gear-btn')) return;

  const instance = getInstance(instanceId);
  if (!instance) return;
  const plugin = getPlugin(instance.type);
  const hasSettings = !!plugin?.settingsSchema?.fields.length;
  if (!hasSettings) return;

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'inst-gear-btn';
  btn.title = `Settings for this ${plugin!.title} instance`;
  btn.textContent = '⚙';
  btn.addEventListener('click', (e) => {
    e.stopPropagation(); // don't fire focus-mode panel click
    _openSettingsDialog(instanceId);
  });

  headerEl.appendChild(btn);
}

// ─── Export / Import section ───────────────────────────────────────────────────

/**
 * Build the workspace Export / Import block (mouse-only).
 *   Export → fills the textarea with the JSON for the user to copy.
 *   Import → reads the textarea, validates + applies, then reloads so every
 *            module rehydrates from the imported localStorage.
 */
// Vestigial (export/import worked on the old disconnected stores); kept exported
// to avoid an unused-symbol error. Not wired into the Modules panel.
export function _buildIoSection(): HTMLElement {
  const section = document.createElement('div');
  section.className = 'mm-io';

  const label = document.createElement('div');
  label.className = 'mm-section-label';
  label.textContent = 'Export / Import workspace';
  section.appendChild(label);

  const textarea = document.createElement('textarea');
  textarea.className = 'mm-io-textarea';
  textarea.placeholder =
    'Click Export to copy your workspace JSON, or paste a config here and click Import.';
  textarea.spellcheck = false;

  const status = document.createElement('div');
  status.className = 'mm-io-status';

  const row = document.createElement('div');
  row.className = 'mm-io-row';

  const exportBtn = document.createElement('button');
  exportBtn.type = 'button';
  exportBtn.className = 'mm-btn';
  exportBtn.textContent = 'Export';
  exportBtn.title = 'Snapshot instances + presets + active board to JSON';
  exportBtn.addEventListener('click', () => {
    textarea.value = exportWorkspace();
    textarea.select();
    status.textContent = 'Exported — select all + copy.';
    status.className = 'mm-io-status is-ok';
  });

  const importBtn = document.createElement('button');
  importBtn.type = 'button';
  importBtn.className = 'mm-btn';
  importBtn.textContent = 'Import';
  importBtn.title = 'Apply a pasted workspace JSON (reloads on success)';
  importBtn.addEventListener('click', () => {
    const res = importWorkspace(textarea.value.trim());
    if (res.ok) {
      status.textContent = 'Imported — reloading…';
      status.className = 'mm-io-status is-ok';
      _notifyChange();
      // Rehydrate every module-level cache (instances/presets/board) from the
      // freshly-written localStorage. A reload is the safe, total way to do it.
      if (typeof location !== 'undefined') {
        setTimeout(() => location.reload(), 250);
      } else {
        section.closest('.mm-backdrop')?.remove();
      }
    } else {
      status.textContent = `Import failed: ${res.error ?? 'invalid config'}`;
      status.className = 'mm-io-status is-err';
    }
  });

  row.appendChild(exportBtn);
  row.appendChild(importBtn);

  section.appendChild(textarea);
  section.appendChild(row);
  section.appendChild(status);
  return section;
}

// ─── Module Manager overlay ────────────────────────────────────────────────────

function _buildManagerOverlay(): HTMLElement {
  _ensureStyles();

  const overlay = document.createElement('div');
  overlay.className = 'mm-overlay';
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-label', 'Module Manager');

  const h2 = document.createElement('h2');
  h2.textContent = 'Modules';
  overlay.appendChild(h2);

  const hint = document.createElement('div');
  hint.className = 'mm-section-label';
  hint.textContent = 'Show or hide panels on the dashboard';
  overlay.appendChild(hint);

  // One Show/Hide toggle per registered panel. Toggling drives the REAL render:
  // the grid renders enabledPlugins(), so flipping a panel's enabled flag makes
  // it appear/disappear on screen immediately (the store re-emit re-runs layout).
  for (const plugin of allPlugins()) {
    const row = document.createElement('div');
    row.className = 'mm-row';

    const lbl = document.createElement('span');
    lbl.className = 'mm-row-label';
    lbl.textContent = plugin.title;

    const tog = document.createElement('button');
    tog.type = 'button';
    const paint = (on: boolean): void => {
      tog.textContent = on ? 'Shown' : 'Hidden';
      tog.className = 'mm-btn' + (on ? ' mm-btn-on' : ' mm-btn-off');
      tog.setAttribute('aria-pressed', on ? 'true' : 'false');
    };
    // DL3: in desktop mode, toggle is per-layout; otherwise global.
    const _isOn = (): boolean =>
      isDesktopActive()
        ? isModuleEnabledForLayout(getLayoutOverride(), plugin.id)
        : isPanelEnabled(plugin.id);

    paint(_isOn());
    tog.addEventListener('click', () => {
      const next = !_isOn();
      if (isDesktopActive()) {
        setModuleEnabledForLayout(getLayoutOverride(), plugin.id, next);
      } else {
        setPanelEnabled(plugin.id, next);
      }
      paint(next);
      _notifyChange();           // → store.emit → _applyLayout re-renders the grid
    });

    // DL: in desktop mode each module is a free window with a z-order, so offer a
    // "Front" button that raises it above the others (useful when a window is
    // buried behind another and can't be clicked). No-op / hidden in grid layouts.
    if (isDesktopActive()) {
      const front = document.createElement('button');
      front.type = 'button';
      front.className = 'mm-btn';
      front.textContent = 'Front';
      front.title = `Bring ${plugin.title} to the front`;
      front.addEventListener('click', () => {
        if (!_isOn()) {
          // Bring-to-front only makes sense for a shown module — enable it first.
          setModuleEnabledForLayout(getLayoutOverride(), plugin.id, true);
          paint(true);
          _notifyChange();
        }
        bringPanelToFront(plugin.id);
        overlay.closest('.mm-backdrop')?.remove(); // close so the raised window is visible
      });
      row.appendChild(front);
    }

    row.appendChild(lbl);
    row.appendChild(tog);
    overlay.appendChild(row);
  }

  // ── Close ───────────────────────────────────────────────────────────────────
  const closeRow = document.createElement('div');
  closeRow.className = 'mm-close-row';

  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'mm-btn';
  closeBtn.textContent = 'Close';
  closeRow.appendChild(closeBtn);
  overlay.appendChild(closeRow);

  // Stored so _refreshManagerOverlay can re-wire it after a redraw
  overlay.dataset['mmType'] = 'manager';
  closeBtn.addEventListener('click', () => {
    overlay.closest('.mm-backdrop')?.remove();
  });

  return overlay;
}


/**
 * Open the Module Manager overlay (floating over the dashboard).
 * Call from a top-bar button or the command palette.
 */
export function openModuleManager(): void {
  _ensureStyles();

  const backdrop = document.createElement('div');
  backdrop.className = 'mm-backdrop';

  const overlay = _buildManagerOverlay();
  backdrop.appendChild(overlay);
  document.body.appendChild(backdrop);

  // Re-wire the Close button from _buildManagerOverlay
  const closeBtn = overlay.querySelector<HTMLButtonElement>('.mm-close-row .mm-btn');
  if (closeBtn) {
    // Remove existing listener and add one that targets the backdrop
    const freshClose = closeBtn.cloneNode(true) as HTMLButtonElement;
    closeBtn.replaceWith(freshClose);
    freshClose.addEventListener('click', () => backdrop.remove());
  }

  backdrop.addEventListener('click', (e) => {
    if (e.target === backdrop) backdrop.remove();
  });
}
