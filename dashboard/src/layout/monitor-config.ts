/**
 * layout/monitor-config.ts — #211 per-monitor configuration.
 *
 * The wallpaper host opens each monitor with ?win=<index>. This module lets you
 * configure each monitor *from that monitor*: designate it primary or secondary,
 * and turn its top bar on or off. Both flags are namespaced by ?win (with a
 * legacy un-suffixed fallback on read) so every screen keeps its own choice,
 * mirroring the desktop/template per-window persistence pattern.
 *
 * Pure-ish: the only DOM it touches is the #topbar element via
 * applyTopbarVisibility(), called once at startup and whenever the flag changes.
 */

export type MonitorRole = 'primary' | 'secondary';

const LS_ROLE = 'dash:monitorRole';
const LS_TOPBAR = 'dash:topbarVisible';

const WIN: string = (() => {
  try { return new URLSearchParams(location.search).get('win') ?? ''; }
  catch { return ''; }
})();
function _winKey(base: string): string { return WIN ? `${base}:${WIN}` : base; }

function _get(base: string): string | null {
  try {
    if (typeof localStorage === 'undefined') return null;
    return localStorage.getItem(_winKey(base)) ?? localStorage.getItem(base);
  } catch { return null; }
}

function _set(base: string, value: string): void {
  try { if (typeof localStorage !== 'undefined') localStorage.setItem(_winKey(base), value); } catch {}
}

/** This monitor's role. Defaults: win '' / '1' → primary, others → secondary. */
export function getMonitorRole(): MonitorRole {
  const raw = _get(LS_ROLE);
  if (raw === 'primary' || raw === 'secondary') return raw;
  return (WIN === '' || WIN === '1') ? 'primary' : 'secondary';
}

export function setMonitorRole(role: MonitorRole): void {
  _set(LS_ROLE, role);
}

/** Whether the top bar shows on this monitor (default: yes). */
export function isTopbarVisible(): boolean {
  const raw = _get(LS_TOPBAR);
  return raw === null ? true : raw === '1';
}

export function setTopbarVisible(on: boolean): void {
  _set(LS_TOPBAR, on ? '1' : '0');
  applyTopbarVisibility();
}

/** Show/hide the #topbar to match this monitor's flag. Safe to call repeatedly. */
export function applyTopbarVisibility(): void {
  if (typeof document === 'undefined') return;
  const bar = document.getElementById('topbar');
  if (bar) bar.style.display = isTopbarVisible() ? '' : 'none';
}
