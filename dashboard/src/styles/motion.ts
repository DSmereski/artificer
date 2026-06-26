/**
 * styles/motion.ts — Motion governor for the fx layer (F1.3)
 *
 * Manages a single `data-motion` attribute on <html>:
 *   "full"  — all fx animations run at normal speed (default, idle tier)
 *   "calm"  — animations run at reduced speed (busy/contended tier)
 *   "off"   — all fx animations stopped (reduced-motion / gaming tier / offline)
 *
 * Sources that can set motion level (highest priority wins):
 *   1. prefers-reduced-motion: reduce  → always "off" (accessibility)
 *   2. RenderTier "gaming" or "offline"→ "off"
 *   3. RenderTier "busy"              → "calm"
 *   4. RenderTier "idle"              → "full"
 *   5. Manual override via setMotion() (used by future settings UI)
 *
 * CSS keys every animation off `data-motion`:
 *   html[data-motion="off"]  .fx-* { animation: none !important }
 *   html[data-motion="calm"] .fx-* { animation-duration: 2× }
 *
 * The module also manages:
 *   - The #fx-backdrop element (ambient backdrop layer, behind the grid)
 *   - Applying .fx-panel-frame to .dashboard-cell elements (panel framing)
 *   - Per-theme backdrop class selection
 */

import type { RenderTier } from '../state/types.js';

// ─── Types ────────────────────────────────────────────────────────────────────

export type MotionLevel = 'full' | 'calm' | 'off';

/** Which fx backdrop effect class to apply for each theme. */
const THEME_BACKDROP: Record<string, string> = {
  'hive-v2':     'fx-honeycomb',
  'holo':        'fx-ambient',
  'terminal':    'fx-scanline',
  'vector-tron': 'fx-grid',
  'glitch-mag':  'fx-ambient',
  'brutalist':   'fx-grid',
  'joker':       'fx-ambient',
  'nod':         'fx-honeycomb',
  'synthwave':   'fx-holo',
  'daybreak':    'fx-ambient',
  'royal':       'fx-ambient',
  'weatherstar': 'fx-grid',
  'retro-purple':'fx-ambient',
  'inverted':    'fx-grid',
  'zombie':      'fx-scanline',
  'code-fall':   'fx-scanline',
  'winter':      'fx-ambient',
  'code-red':    'fx-ambient',
};

// ─── State ────────────────────────────────────────────────────────────────────

let _backdropEl: HTMLElement | null = null;
let _currentLevel: MotionLevel = 'full';
let _manualOverride: MotionLevel | null = null;
let _prefersReduced = false;
let _tierLevel: MotionLevel = 'full';
let _fancyEnabled = false;

// ─── Core setter ─────────────────────────────────────────────────────────────

/**
 * Apply a motion level to <html data-motion>.
 * Idempotent — only touches the DOM when the value changes.
 */
export function setMotion(level: MotionLevel): void {
  _currentLevel = level;
  // DOM write is a no-op in node (tests run without document)
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  if (root.dataset['motion'] !== level) {
    root.dataset['motion'] = level;
  }
}

/** Read the current motion level. */
export function getMotion(): MotionLevel {
  return _currentLevel;
}

// ─── Priority resolver ────────────────────────────────────────────────────────

function _resolve(): void {
  // Manual override trumps everything
  if (_manualOverride !== null) {
    setMotion(_manualOverride);
    return;
  }
  // Accessibility: reduced-motion → always off
  if (_prefersReduced) {
    setMotion('off');
    return;
  }
  // Tier-driven
  setMotion(_tierLevel);
}

// ─── Public API ───────────────────────────────────────────────────────────────

/**
 * Called by the main engine when the RenderTier changes.
 * Maps tier → motion level with the priority rules above.
 */
export function onTierChange(tier: RenderTier): void {
  switch (tier) {
    case 'gaming':
    case 'offline':
      _tierLevel = 'off';
      break;
    case 'busy':
      _tierLevel = 'calm';
      break;
    case 'idle':
    default:
      _tierLevel = 'full';
      break;
  }
  _resolve();
}

/**
 * Allow the user (or settings UI) to pin a specific motion level.
 * Pass null to clear the override and return to automatic.
 */
export function setMotionOverride(level: MotionLevel | null): void {
  _manualOverride = level;
  _resolve();
}

/** Whether fancy (fx-panel-frame + backdrop) is currently active. */
export function isFancyEnabled(): boolean {
  return _fancyEnabled;
}

/**
 * Enable or disable the fancy fx-panel-frame treatment on all .dashboard-cell
 * elements. Safe to call multiple times — idempotent.
 */
export function setFancy(enabled: boolean): void {
  if (typeof document === 'undefined') return;
  _fancyEnabled = enabled;
  const cells = document.querySelectorAll<HTMLElement>('.dashboard-cell');
  for (const cell of cells) {
    if (enabled) {
      cell.classList.add('fx-panel-frame');
    } else {
      cell.classList.remove('fx-panel-frame');
    }
  }
  // Also wire the MutationObserver so new cells also get the class.
  if (enabled) {
    _startCellObserver();
  } else {
    _stopCellObserver();
  }
}

// ─── Backdrop layer ───────────────────────────────────────────────────────────

/**
 * Create (once) the #fx-backdrop element and insert it as the first child of
 * <body>, behind everything else.
 */
function _ensureBackdrop(): HTMLElement {
  if (_backdropEl) return _backdropEl;
  const el = document.createElement('div');
  el.id = 'fx-backdrop';
  // Insert before the first child so it's truly behind everything
  const body = document.body;
  body.insertBefore(el, body.firstChild);
  _backdropEl = el;
  return el;
}

/**
 * Update the backdrop's effect class based on the current theme.
 * Called on init and whenever the hive-theme-change event fires.
 */
function _applyBackdropTheme(): void {
  if (typeof document === 'undefined') return;
  const theme = document.documentElement.dataset['theme'] ?? 'holo';
  const effectClass = THEME_BACKDROP[theme] ?? 'fx-ambient';
  const el = _ensureBackdrop();

  // Remove any previous fx backdrop class
  for (const cls of Array.from(el.classList)) {
    if (cls.startsWith('fx-')) el.classList.remove(cls);
  }
  el.classList.add(effectClass);
}

// ─── Cell MutationObserver (for fx-panel-frame on dynamically-added cells) ────

let _cellObserver: MutationObserver | null = null;

function _startCellObserver(): void {
  if (_cellObserver || typeof MutationObserver === 'undefined') return;
  const grid = document.getElementById('dashboard-grid');
  if (!grid) return;
  _cellObserver = new MutationObserver((mutations) => {
    for (const m of mutations) {
      for (const node of m.addedNodes) {
        if (node instanceof HTMLElement && node.classList.contains('dashboard-cell')) {
          if (_fancyEnabled) node.classList.add('fx-panel-frame');
        }
      }
    }
  });
  _cellObserver.observe(grid, { childList: true });
}

function _stopCellObserver(): void {
  _cellObserver?.disconnect();
  _cellObserver = null;
}

// ─── Initialisation ───────────────────────────────────────────────────────────

/**
 * Auto-init: call once at application start.
 *
 *  - Reads prefers-reduced-motion and registers a listener.
 *  - Sets initial data-motion on <html>.
 *  - Creates and themes the #fx-backdrop.
 *  - Listens for hive-theme-change events to re-theme the backdrop.
 *  - Enables fancy panel frames by default.
 */
export function initMotionGovernor(): void {
  if (typeof window === 'undefined') return;

  // Detect prefers-reduced-motion
  const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
  _prefersReduced = mq.matches;
  mq.addEventListener('change', (e) => {
    _prefersReduced = e.matches;
    _resolve();
  });

  // Set initial level
  _resolve();

  // Backdrop
  _applyBackdropTheme();

  // Re-theme backdrop on theme change
  window.addEventListener('hive-theme-change', () => {
    _applyBackdropTheme();
  });

  // Enable fancy panel frames by default
  setFancy(true);
}

// ─── Test-friendly helpers ────────────────────────────────────────────────────

/** Reset all internal state (for unit tests). */
export function _resetForTest(): void {
  _currentLevel      = 'full';
  _manualOverride    = null;
  _prefersReduced    = false;
  _tierLevel         = 'full';
  _fancyEnabled      = false;
  _backdropEl        = null;
  _cellObserver?.disconnect();
  _cellObserver      = null;
}

/** Directly inject prefers-reduced state (for unit tests). */
export function _setReducedMotionForTest(reduced: boolean): void {
  _prefersReduced = reduced;
  _resolve();
}
