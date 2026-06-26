/**
 * plugins/index.ts — Self-register barrel.
 *
 * Import this module to trigger all plugin self-registrations.
 * Adding a new panel = dropping a file + adding ONE import line here.
 * No other core files need editing.
 */

export * from './crew-board.js';
export * from './crew-board-full.js';
// needs-you.js is intentionally NOT exported (P5 v-Next): its review/escalation
// rows are now folded into the Activity feed as priority-flagged, clickable rows
// (see activity-feed.ts). Registering the standalone NEEDS YOU panel here would
// double-surface those items. The file is kept in place but no longer barreled.
// The CC2 klaxon (alerts.ts → trackEscalations) remains a separate path.
export * from './system.js';
export * from './docker.js';
export * from './kpi.js';

// Unified activity feed: replaces actions-log + git-activity panels.
export * from './activity-feed.js';
export * from './content-gallery.js';
export * from './gpu.js';
export * from './telemetry.js';
export * from './graph.js';
export * from './escalations.js';
export * from './agenda.js';
export * from './suno.js';

// Demo panel (Pv4): clock/uptime
export * from './clock.js';

// Tokens-per-day chart is folded into the Telemetry panel (plugins/telemetry.ts)
// for the default template layout. The standalone tokens-day panel is now an
// opt-in, multi-instance module (v-Next P4) with a per-instance `range` setting
// (7d/30d/90d) + inline control — created via the Module Manager, not the
// default grid. Registering it here makes the plugin type available to instances.
export * from './tokens-day.js';

// PowerShell PTY-over-WS terminal panel
export * from './terminal.js';

// Wiki review rail — surfaces contradictions + gaps from wiki_synth (C4).
export * from './wiki-review.js';

// Crew Board Manager toggle panel
export * from './manager-toggle.js';

// Open-Meteo weather module (v-Next P3) — keyless, opt-in, multi-instance.
export * from './weather.js';

// Projects management — workload + On/Off per project + Evolve (Suggest / Go
// do more) on finished ones. Replaces the old in-board Evolve lane.
export * from './projects.js';
