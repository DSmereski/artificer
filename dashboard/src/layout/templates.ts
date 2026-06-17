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
// Z-pattern: the board (left) is context, the KPI/hero band tops the centre,
// telemetry + system cluster on the right, one wide terminal grounds the bottom.
const ULTRAWIDE_ROWS = [
  ['kpi',   'kpi',   'kpi',     'kpi',     'kpi',    'kpi',    'kpi',    'kpi'],
  ['board', 'board', 'actions', 'actions', 'telem',  'telem',  'gpu',    'gpu'],
  ['board', 'board', 'actions', 'actions', 'telem',  'telem',  'sys',    'sys'],
  ['board', 'board', 'git',     'git',     'tokens', 'tokens', 'docker', 'docker'],
  ['board', 'board', 'content', 'content', 'agenda', 'needs',  'esc',    'esc'],
  ['term',  'term',  'term',    'term',    'term',   'term',   'term',   'term'],
];

const ULTRAWIDE: Template = {
  name: 'ultrawide',
  columns: 'repeat(8, 1fr)',
  rows: '1.15fr 1fr 1fr 1fr 1fr 1.3fr',
  areas: areasOf(ULTRAWIDE_ROWS),
  slots: {
    kpi: 'kpi',
    'crew-board-full': 'board',
    // Terminal takes the tall 2-col×2-row block (less landscape); the actions
    // log takes the wide bottom strip — swapped per the taller-terminal ask.
    terminal: 'actions',
    telemetry: 'telem',
    gpu: 'gpu',
    system: 'sys',
    'git-activity': 'git',
    'tokens-day': 'tokens',
    docker: 'docker',
    'content-gallery': 'content',
    agenda: 'agenda',
    'needs-you': 'needs',
    escalations: 'esc',
    'actions-log': 'term',
  },
};

// ─── Wide 16:9 (1080 / 1440 / 4K) — condensed ────────────────────────────────
// Fewer simultaneous panels: hero band, the board, a right telemetry stack, a
// wide terminal. Lower-value panels (content/agenda/needs/esc) drop off here.
const WIDE_ROWS = [
  ['kpi',   'kpi',   'kpi',    'kpi'],
  ['board', 'board', 'telem',  'gpu'],
  ['board', 'board', 'git',    'sys'],
  ['board', 'board', 'tokens', 'docker'],
  ['term',  'term',  'term',   'term'],
];

const WIDE: Template = {
  name: 'wide',
  columns: 'repeat(4, 1fr)',
  rows: '1.1fr 1fr 1fr 1fr 1.25fr',
  areas: areasOf(WIDE_ROWS),
  slots: {
    kpi: 'kpi',
    'crew-board-full': 'board',
    telemetry: 'telem',
    gpu: 'gpu',
    'git-activity': 'git',
    system: 'sys',
    'tokens-day': 'tokens',
    docker: 'docker',
    terminal: 'term',
  },
};

// ─── Portrait — single column stack ──────────────────────────────────────────
const PORTRAIT_ROWS = [
  ['kpi'],
  ['board'],
  ['board'],
  ['telem'],
  ['gpu'],
  ['term'],
];

const PORTRAIT: Template = {
  name: 'portrait',
  columns: '1fr',
  rows: 'auto 1.4fr 1.4fr 1fr 1fr 1.2fr',
  areas: areasOf(PORTRAIT_ROWS),
  slots: {
    kpi: 'kpi',
    'crew-board-full': 'board',
    telemetry: 'telem',
    gpu: 'gpu',
    terminal: 'term',
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
