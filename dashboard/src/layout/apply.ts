/**
 * layout/apply.ts — places panels into a hand-designed template's named grid
 * areas (P0 of the v3 redesign). Replaces the auto-packer applier.
 *
 * The container is a CSS grid whose columns/rows/areas come from the active
 * `Template`. Each visible panel gets `grid-area: <name>` and is clipped to its
 * cell. A focused panel spans the whole grid; everything else hides.
 *
 * Visibility = the panel has a slot in this template AND its relevance() is not
 * 'hidden' (relevance is used only as a boolean gate now — never for sizing).
 */

import type { PanelPlugin } from '../plugins/contract.js';
import type { SystemState } from '../state/types.js';
import type { Template } from './templates.js';

let _container: HTMLElement | null = null;
const _cellEls = new Map<string, HTMLElement>(); // id → DOM element
let _appliedTemplate: string | null = null;

const FOCUS_AREA = '1 / 1 / -1 / -1'; // span the whole grid

export function initLayoutApplier(container: HTMLElement): void {
  _container = container;
  container.style.cssText = [
    'display: grid',
    'gap: 10px',
    'padding: 10px 14px 14px',
    'box-sizing: border-box',
    'width: 100%',
    'height: 100%',
    'overflow: hidden',
  ].join('; ');
}

export function isPanelVisible(p: PanelPlugin, state: SystemState): boolean {
  // CC7 error boundary: a panel that throws in relevance() is treated as hidden,
  // never breaks the layout.
  try {
    const rel = p.relevance(state);
    return rel.size !== 'hidden' && (rel.priority ?? 0) >= 10;
  } catch (err) {
    console.error(`[layout] ${p.id}.relevance threw:`, err);
    return false;
  }
}

/**
 * Apply a template. Pure-ish: only touches the DOM, never computes layout.
 * `focusId` (if set) makes that panel fill the whole grid; all others hide.
 */
export function applyTemplate(
  tpl: Template,
  plugins: PanelPlugin[],
  state: SystemState,
  focusId: string | null = null,
): void {
  if (!_container) return;

  // Grid tracks + areas change only when the template itself changes.
  if (_appliedTemplate !== tpl.name) {
    _container.style.gridTemplateColumns = tpl.columns;
    _container.style.gridTemplateRows = tpl.rows;
    _container.style.gridTemplateAreas = tpl.areas;
    _appliedTemplate = tpl.name;
  }

  for (const p of plugins) {
    const slot = focusId
      ? (p.id === focusId ? FOCUS_AREA : null)
      : (tpl.slots[p.id] ?? null);
    const visible = slot != null && (focusId != null || isPanelVisible(p, state));

    let el = _cellEls.get(p.id);
    const wasHidden = !el || el.style.display === 'none';

    if (visible) {
      if (!el) {
        el = _ensureCell(p.id);
        try {
          p.mount(el);
          p.resume?.();
        } catch (err) {
          console.error(`[panel] ${p.id}.mount threw:`, err);
          el.innerHTML =
            `<div class="panel-header"><span class="panel-label">${p.id}</span></div>` +
            `<p class="offline-state">panel error — see console</p>`;
        }
      } else if (wasHidden) {
        p.resume?.(); // hidden → visible: wake it
      }
      el.style.display = '';
      el.style.gridArea = slot as string;
      el.style.minHeight = '0';
      el.style.minWidth = '0';
      el.style.overflow = 'hidden';
    } else if (el && !wasHidden) {
      el.style.display = 'none';
      p.suspend?.();
    }
  }

  // Notify visible panels of their real pixel size next frame (uPlot/xterm).
  requestAnimationFrame(() => {
    for (const p of plugins) {
      const el = _cellEls.get(p.id);
      if (!el || el.style.display === 'none') continue;
      const r = el.getBoundingClientRect();
      try {
        p.onResize?.({ x: r.left, y: r.top, w: r.width, h: r.height });
      } catch {
        /* panel onResize is best-effort */
      }
    }
  });
}

// ─── Cell DOM builder ─────────────────────────────────────────────────────────

function _ensureCell(id: string): HTMLElement {
  if (_cellEls.has(id)) return _cellEls.get(id)!;
  if (!_container) throw new Error('[layout/apply] container not initialised');

  const el = document.createElement('div');
  el.className = 'dashboard-cell';
  el.id = `cell-${id}`;
  el.setAttribute('data-plugin-id', id);
  _container.appendChild(el);
  _cellEls.set(id, el);
  return el;
}

/** Get the DOM element for a cell (or null). */
export function getCellEl(id: string): HTMLElement | null {
  return _cellEls.get(id) ?? null;
}
