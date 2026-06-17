/**
 * plugins/index.ts — Self-register barrel.
 *
 * Import this module to trigger all plugin self-registrations.
 * Adding a new panel = dropping a file + adding ONE import line here.
 * No other core files need editing.
 */

export * from './crew-board.js';
export * from './crew-board-full.js';
export * from './needs-you.js';
export * from './system.js';
export * from './docker.js';
export * from './actions-log.js';
export * from './kpi.js';
export * from './git-activity.js';
export * from './content-gallery.js';
export * from './gpu.js';
export * from './telemetry.js';
export * from './graph.js';
export * from './escalations.js';
export * from './agenda.js';
export * from './suno.js';

// Demo panel (Pv4): clock/uptime
export * from './clock.js';

// Tokens-per-day line chart (hive vs claude, last 30 days)
export * from './tokens-day.js';

// PowerShell PTY-over-WS terminal panel
export * from './terminal.js';
