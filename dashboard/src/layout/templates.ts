/**
 * layout/templates.ts — Hand-designed fixed layouts (P0 of the v3 redesign).
 *
 * Replaces the relevance auto-packer (engine.ts). Each screen-class gets a
 * deliberate CSS `grid-template-areas` map; every panel is placed by hand into
 * a named area. Panels with no area on a given screen-class are simply not
 * shown there — intentional, not "hidden because the packer ran out of room".
 *
 * `pickTemplate(w, h)` chooses by aspect ratio: portrait (taller than wide) →
 * ultrawide (32:9-ish, ratio >= 2.4) → wide (everything else, i.e. 16:9).
 *
 * Pure data + a pure picker. No DOM. Unit-testable.
 *
 * v3 layout: kpi top band removed (info moved to topbar); bottom terminal/log
 * row removed (activity-feed merged panel handles both git + actions). The
 * remaining content rows use 1fr each so the grid fills 100% viewport height
 * with no dead space.
 *
 * tokens-day panel removed: the chart is now folded into the Telemetry panel.
 * The `tokens` area/slot has been dropped and adjacent panels rebalanced to
 * fill the vacated cell (no dead space).
 */

export type TemplateName = 'ultrawide' | 'wide' | 'portrait';

export interface Template {
  name: TemplateName;
  /** CSS grid-template-columns value. */
  columns: string;
  /** CSS grid-template-rows value. */
  rows: string;
  /** CSS grid-template-areas value (one quoted string per row). */
  areas: string;
  /** panelId -> grid-area name. A panel absent here is not shown on this class. */
  slots: Record<string, string>;
}

/** Build the `grid-template-areas` string from a row-of-tokens matrix. */
function areasOf(rows: string[][]): string {
  return rows.map((r) => `"${r.join(' ')}"`).join('\n');
}

// ─── Ultrawide 5440×1440 (32:9) — the "command spine" ─────────────────────────
// Board-dominant layout: the crew board owns 5 of 8 columns (≈ 62% of width)
// across all 4 rows, making it the unmistakable primary surface.
// Right 3 columns hold the secondary panels in a compact cluster.
// kpi top band is gone — swarm pulse lives in the topbar.
// Bottom terminal row is gone — activity-feed consolidates both signal types.
// All 4 content rows are 1fr so they fully fill the viewport height.
// tokens-day area removed — chart lives inside the Telemetry panel (row 3
// vacancy filled by extending docker from row 3 onward, matching row 4).
const ULTRAWIDE_ROWS = [
  ['board', 'board', 'board', 'board', 'board', 'actions', 'telem',  'gpu'],
  ['board', 'board', 'board', 'board', 'board', 'actions', 'telem',  'sys'],
  ['board', 'board', 'board', 'board', 'board', 'activity','docker', 'docker'],
  ['board', 'board', 'board', 'board', 'board', 'activity','needs',  'esc'],
];

const ULTRAWIDE: Template = {
  name: 'ultrawide',
  columns: 'repeat(8, 1fr)',
  rows: '1fr 1fr 1fr 1fr',
  areas: areasOf(ULTRAWIDE_ROWS),
  slots: {
    'crew-board-full': 'board',
    terminal:          'actions',
    telemetry:         'telem',
    gpu:               'gpu',
    system:            'sys',
    'activity-feed':   'activity',
    docker:            'docker',
    'needs-you':       'needs',
    escalations:       'esc',
  },
};

// ─── Wide 16:9 (1080 / 1440 / 4K) — condensed ────────────────────────────────
// kpi row removed; 3 content rows + terminal row fill the height.
// activity-feed takes the middle-right slot (was git, now combined).
// tokens-day area removed — chart lives inside the Telemetry panel; the
// vacated row-3 cell is filled by extending activity-feed down one row.
const WIDE_ROWS = [
  ['board', 'board', 'telem',    'gpu'],
  ['board', 'board', 'activity', 'sys'],
  ['board', 'board', 'activity', 'docker'],
  ['board', 'board', 'terminal', 'terminal'],
];

const WIDE: Template = {
  name: 'wide',
  columns: 'repeat(4, 1fr)',
  rows: '1fr 1fr 1fr min-content',
  areas: areasOf(WIDE_ROWS),
  slots: {
    'crew-board-full': 'board',
    telemetry:         'telem',
    gpu:               'gpu',
    'activity-feed':   'activity',
    system:            'sys',
    docker:            'docker',
    terminal:          'terminal',
  },
};

// ─── Portrait — single column stack ──────────────────────────────────────────
const PORTRAIT_ROWS = [
  ['board'],
  ['board'],
  ['telem'],
  ['gpu'],
  ['terminal'],
];

const PORTRAIT: Template = {
  name: 'portrait',
  columns: '1fr',
  rows: '1fr 1fr 1fr min-content min-content',
  areas: areasOf(PORTRAIT_ROWS),
  slots: {
    'crew-board-full': 'board',
    telemetry:         'telem',
    gpu:               'gpu',
    terminal:          'terminal',
  },
};

export const TEMPLATES: Record<TemplateName, Template> = {
  ultrawide: ULTRAWIDE,
  wide: WIDE,
  portrait: PORTRAIT,
};

/** Choose the template for a viewport. Pure. */
export function pickTemplate(w: number, h: number): Template {
  if (h > w) return PORTRAIT;
  const ratio = w / Math.max(1, h);
  if (ratio >= 2.4) return ULTRAWIDE; // 32:9 ≈ 3.78; 21:9 ≈ 2.37 stays wide
  return WIDE;
}
