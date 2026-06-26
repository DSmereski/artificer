/**
 * plugins/content-gallery.ts — Content Gallery (image/video requests).
 *
 * Shows content board tasks (kind='content'): the prompt, generation state,
 * and result thumbnails. Click a thumbnail to enlarge. Fed by a bridge from
 * the board poll (content tasks come through /board/state with content_spec).
 */

import { register } from './registry.js';
import type { PanelPlugin, RelevanceResult } from './contract.js';
import type { SystemState, RenderBudget } from '../state/types.js';
import type { BoardTask } from '../gateway.js';
import { mediaUrl } from '../gateway.js';
import { escHtml } from '../format.js';
import { resolveSettings } from './instances.js';

// ─── Settings ─────────────────────────────────────────────────────────────────

interface GallerySettings {
  maxItems: number;
}

/**
 * Default 24 == today's behavior (the old `onContentBoard` `slice(0, 24)`).
 * Set lower to show fewer thumbnails. A fresh user sees no change.
 */
const DEFAULT_SETTINGS: GallerySettings = { maxItems: 24 };

let _items: BoardTask[] = [];
let _rootEl: HTMLElement | null = null;

export function onContentBoard(tasks: BoardTask[]): void {
  _items = tasks
    .filter((t) => t.kind === 'content')
    .sort((a, b) => (b.updated_at ?? '').localeCompare(a.updated_at ?? ''))
    .slice(0, 24);
  _render();
}

function _generating(): number {
  return _items.filter((t) => t.status === 'in_progress' || t.status === 'ready').length;
}

function relevance(state: SystemState): RelevanceResult {
  if (state.tier === 'gaming') return { priority: 0, size: 'hidden' };
  if (_items.length === 0) return { priority: 0, size: 'hidden' };
  // Rise while something is generating so you can watch it land.
  return { priority: _generating() > 0 ? 72 : 50, size: 'md' };
}

function mount(el: HTMLElement): void {
  _rootEl = el;
  el.innerHTML = `
    <div class="panel-header">
      <span class="panel-label">CONTENT GALLERY</span>
      <span class="cg-count" id="cg-count"></span>
    </div>
    <div id="cg-grid" class="cg-grid"></div>
  `;
  el.addEventListener('click', (e) => {
    const img = (e.target as HTMLElement)?.closest<HTMLElement>('.cg-thumb[data-media]');
    if (img) _openLightbox(img.dataset['media'] as string);
  });
  _render();
}

function _render(): void {
  if (!_rootEl) return;
  const grid = _rootEl.querySelector('#cg-grid') as HTMLElement | null;
  const count = _rootEl.querySelector('#cg-count') as HTMLElement | null;
  if (!grid) return;
  if (count) count.textContent = _items.length ? String(_items.length) : '';

  if (_items.length === 0) {
    grid.innerHTML = '<p class="offline-state">No content yet. Use ⌘ → New image.</p>';
    return;
  }

  const { maxItems } = resolveSettings(_rootEl, 'content-gallery', DEFAULT_SETTINGS);
  const cap = maxItems > 0 ? maxItems : DEFAULT_SETTINGS.maxItems;

  grid.innerHTML = _items.slice(0, cap).map((t) => {
    const spec = t.content_spec ?? {};
    const media = spec.result_media_ids ?? [];
    const done = t.status === 'done' && media.length > 0;
    const failed = t.status === 'review' || spec.state === 'error';
    const typeTag = spec.type === 'video' ? '▶' : '▣';

    // F3: pending (non-failed) items get shimmer sweep to signal "actively generating"
    const thumbs = done
      ? media.map((id) => `<img class="cg-thumb" data-media="${escHtml(id)}" src="${mediaUrl(id)}" alt="" loading="lazy" />`).join('')
      : `<div class="cg-pending ${failed ? 'cg-failed' : 'fx3-shimmer'}">${failed ? '✕ failed' : '◴ generating…'}</div>`;

    return `
      <div class="cg-item">
        <div class="cg-thumbs">${thumbs}</div>
        <div class="cg-meta">
          <span class="cg-type">${typeTag}</span>
          <span class="cg-prompt" title="${escHtml(spec.prompt ?? t.title)}">${escHtml(spec.prompt ?? t.title)}</span>
        </div>
      </div>`;
  }).join('');
}

function _openLightbox(id: string): void {
  if (!id || typeof document === 'undefined') return;
  let lb = document.getElementById('cg-lightbox');
  if (!lb) {
    lb = document.createElement('div');
    lb.id = 'cg-lightbox';
    lb.className = 'cg-lightbox';
    lb.addEventListener('click', () => { lb!.hidden = true; lb!.innerHTML = ''; });
    document.body.appendChild(lb);
  }
  lb.innerHTML = `<img src="${mediaUrl(id)}" alt="" />`;
  lb.hidden = false;
}

function update(_state: SystemState, _budget: RenderBudget): void {
  // Driven by the content bridge.
}

const contentGalleryPlugin: PanelPlugin = {
  id:          'content-gallery',
  title:       'CONTENT GALLERY',
  dataSources: [{ kind: 'state' }],
  relevance,
  mount,
  update,
  defaultSettings: { ...DEFAULT_SETTINGS },
  settingsSchema: {
    fields: [
      {
        key: 'maxItems',
        label: 'Max items',
        type: 'number',
        default: 24,
        hint: 'How many content cards to show',
      },
    ],
  },
};

register(contentGalleryPlugin);
export { contentGalleryPlugin };
