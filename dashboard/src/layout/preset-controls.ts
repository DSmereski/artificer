/**
 * layout/preset-controls.ts — Preset switcher UI for P1 free-form layout.
 *
 * Provides a floating overlay (accessible from a top-bar button or the
 * Module Manager) where the user can:
 *   - See all saved presets.
 *   - Save the current arrangement as a new named preset.
 *   - Switch to a preset (or back to the template layout).
 *   - Delete a preset.
 *
 * Mouse-only interactions (Lively keyboard constraint).
 * Deliberately minimal styling — reuses `.mm-*` classes from module-manager.ts.
 */

import {
  allPresets,
  savePreset,
  deletePreset,
  switchPreset,
  activePresetId,
  isFreeformActive,
  TEMPLATE_PRESET_ID,
  type PresetGeometryEntry,
} from './presets.js';
import { allInstances } from '../plugins/instances.js';
import type { InstanceGeometry } from '../plugins/instances.js';

// ─── Re-layout callback ────────────────────────────────────────────────────────

let _onChangeCallback: (() => void) | null = null;

/** Register the callback that triggers a full layout re-apply after a switch. */
export function setPresetChangeCallback(fn: () => void): void {
  _onChangeCallback = fn;
}

function _notifyChange(): void {
  _onChangeCallback?.();
}

// ─── Helpers ───────────────────────────────────────────────────────────────────

/** Collect current geometries from the instance store. */
function _collectCurrentGeometries(): PresetGeometryEntry[] {
  return allInstances()
    .filter((i) => i.geometry != null)
    .map((i) => ({
      instanceId: i.instanceId,
      geometry: { ...(i.geometry as InstanceGeometry) },
    }));
}

// ─── Styles (reuse .mm-* from module-manager; add minimal extras once) ─────────

let _stylesInjected = false;

function _ensureStyles(): void {
  if (_stylesInjected) return;
  _stylesInjected = true;
  const s = document.createElement('style');
  s.textContent = `
.preset-active-badge {
  display: inline-block;
  font-size: 9px;
  letter-spacing: .1em;
  background: var(--amber, #c07840);
  color: #000;
  border-radius: 3px;
  padding: 1px 5px;
  margin-left: 6px;
  text-transform: uppercase;
  vertical-align: middle;
}
.preset-save-row {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-top: 8px;
}
.preset-save-row input[type="text"] {
  flex: 1;
  background: var(--bg, #111);
  border: 1px solid var(--line, #444);
  border-radius: 4px;
  color: var(--fg, #ccc);
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  padding: 4px 8px;
}
`;
  document.head.appendChild(s);
}

// ─── Overlay builder ───────────────────────────────────────────────────────────

function _buildOverlay(): HTMLElement {
  _ensureStyles();

  const overlay = document.createElement('div');
  overlay.className = 'mm-overlay';
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-label', 'Layout Presets');

  const h2 = document.createElement('h2');
  h2.textContent = 'Layout Presets';
  overlay.appendChild(h2);

  // ── Active status ──────────────────────────────────────────────────────────
  const statusLine = document.createElement('div');
  statusLine.style.cssText = 'font-size:11px;color:var(--dim,#666);margin-bottom:10px;';
  const activeId = activePresetId();
  if (activeId === TEMPLATE_PRESET_ID) {
    statusLine.textContent = 'Active: Template layout (default)';
  } else {
    const p = allPresets().find((x) => x.id === activeId);
    statusLine.textContent = `Active: "${p?.name ?? activeId}"`;
  }
  overlay.appendChild(statusLine);

  // ── Saved presets ──────────────────────────────────────────────────────────
  const presets = allPresets();
  if (presets.length === 0) {
    const note = document.createElement('div');
    note.className = 'mm-section-label';
    note.textContent = 'No presets saved yet';
    overlay.appendChild(note);
  } else {
    const existLabel = document.createElement('div');
    existLabel.className = 'mm-section-label';
    existLabel.textContent = 'Saved presets';
    overlay.appendChild(existLabel);

    for (const preset of presets) {
      const row = document.createElement('div');
      row.className = 'mm-row';

      const lbl = document.createElement('span');
      lbl.className = 'mm-row-label';
      lbl.textContent = preset.name;
      if (preset.id === activePresetId()) {
        const badge = document.createElement('span');
        badge.className = 'preset-active-badge';
        badge.textContent = 'active';
        lbl.appendChild(badge);
      }

      const switchBtn = document.createElement('button');
      switchBtn.type = 'button';
      switchBtn.className = 'mm-btn';
      switchBtn.textContent = preset.id === activePresetId() ? 'Reload' : 'Load';
      switchBtn.title = `Switch to preset "${preset.name}"`;
      switchBtn.addEventListener('click', () => {
        switchPreset(preset.id);
        _notifyChange();
        _refreshOverlay(overlay);
      });

      const delBtn = document.createElement('button');
      delBtn.type = 'button';
      delBtn.className = 'mm-btn mm-btn-remove';
      delBtn.textContent = 'Delete';
      delBtn.title = `Delete preset "${preset.name}"`;
      delBtn.addEventListener('click', () => {
        deletePreset(preset.id);
        _notifyChange();
        _refreshOverlay(overlay);
      });

      row.appendChild(lbl);
      row.appendChild(switchBtn);
      row.appendChild(delBtn);
      overlay.appendChild(row);
    }
  }

  // ── Return to template ─────────────────────────────────────────────────────
  if (isFreeformActive()) {
    const tplSection = document.createElement('div');
    tplSection.className = 'mm-section-label';
    tplSection.style.marginTop = '12px';
    tplSection.textContent = 'Template layout';
    overlay.appendChild(tplSection);

    const tplRow = document.createElement('div');
    tplRow.className = 'mm-row';

    const tplLbl = document.createElement('span');
    tplLbl.className = 'mm-row-label';
    tplLbl.textContent = 'Return to fixed template layout';

    const tplBtn = document.createElement('button');
    tplBtn.type = 'button';
    tplBtn.className = 'mm-btn';
    tplBtn.textContent = 'Use Template';
    tplBtn.addEventListener('click', () => {
      switchPreset(TEMPLATE_PRESET_ID);
      _notifyChange();
      _refreshOverlay(overlay);
    });

    tplRow.appendChild(tplLbl);
    tplRow.appendChild(tplBtn);
    overlay.appendChild(tplRow);
  }

  // ── Save current arrangement ───────────────────────────────────────────────
  const saveSection = document.createElement('div');
  saveSection.className = 'mm-section-label';
  saveSection.style.marginTop = '12px';
  saveSection.textContent = 'Save current arrangement';
  overlay.appendChild(saveSection);

  const saveRow = document.createElement('div');
  saveRow.className = 'preset-save-row';

  const nameInput = document.createElement('input');
  nameInput.type = 'text';
  nameInput.placeholder = 'Preset name…';
  nameInput.value = '';

  const saveBtn = document.createElement('button');
  saveBtn.type = 'button';
  saveBtn.className = 'mm-btn';
  saveBtn.textContent = 'Save';
  saveBtn.addEventListener('click', () => {
    const name = nameInput.value.trim() || `Preset ${allPresets().length + 1}`;
    const geoms = _collectCurrentGeometries();
    const preset = savePreset(name, geoms);
    // Auto-switch to the new preset.
    switchPreset(preset.id);
    _notifyChange();
    _refreshOverlay(overlay);
  });

  saveRow.appendChild(nameInput);
  saveRow.appendChild(saveBtn);
  overlay.appendChild(saveRow);

  // ── Note about free-form mode ──────────────────────────────────────────────
  if (!isFreeformActive()) {
    const hint = document.createElement('p');
    hint.style.cssText =
      'font-size:10px;color:var(--dim,#666);margin-top:10px;line-height:1.5;';
    hint.textContent =
      'Saving a preset will switch the dashboard into free-form mode where ' +
      'you can drag and resize panels with the mouse. Save with at least one ' +
      'module instance configured.';
    overlay.appendChild(hint);
  }

  // ── Close button ───────────────────────────────────────────────────────────
  const closeRow = document.createElement('div');
  closeRow.className = 'mm-close-row';
  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'mm-btn';
  closeBtn.textContent = 'Close';
  // Wired in openPresetControls to reference the backdrop.
  closeRow.appendChild(closeBtn);
  overlay.appendChild(closeRow);

  overlay.dataset['pcType'] = 'presets';
  return overlay;
}

function _refreshOverlay(existing: HTMLElement): void {
  const backdrop = existing.closest('.mm-backdrop');
  if (!backdrop) return;
  const fresh = _buildOverlay();
  // Re-wire the Close button.
  const freshClose = fresh.querySelector<HTMLButtonElement>('.mm-close-row .mm-btn');
  freshClose?.addEventListener('click', () => backdrop.remove());
  backdrop.replaceChild(fresh, existing);
}

// ─── Public entrypoint ─────────────────────────────────────────────────────────

/**
 * Open the Preset Controls overlay.
 * Reuses the `.mm-backdrop` class (same styles as the Module Manager).
 */
export function openPresetControls(): void {
  _ensureStyles();

  const backdrop = document.createElement('div');
  backdrop.className = 'mm-backdrop';

  const overlay = _buildOverlay();
  backdrop.appendChild(overlay);
  document.body.appendChild(backdrop);

  // Wire Close button.
  const closeBtn = overlay.querySelector<HTMLButtonElement>('.mm-close-row .mm-btn');
  if (closeBtn) {
    const fresh = closeBtn.cloneNode(true) as HTMLButtonElement;
    closeBtn.replaceWith(fresh);
    fresh.addEventListener('click', () => backdrop.remove());
  }

  backdrop.addEventListener('click', (e) => {
    if (e.target === backdrop) backdrop.remove();
  });
}
