/**
 * layout/focus.ts — CC5 focus mode.
 *
 * Click any panel header → that panel takes the whole grid (hero), every other
 * panel hides; Esc or clicking the header again restores the adaptive layout.
 * Generalizes the crew-board-full toggle to ALL panels.
 *
 * The layout engine reads focusedId() and, when set, overrides relevance:
 * focused → hero, others → hidden. A toggle nudges the store to re-layout
 * (focus is a user action, not a state change).
 */

let _focusedId: string | null = null;
let _nudge: (() => void) | null = null;

export function focusedId(): string | null {
  return _focusedId;
}

export function setFocusNudge(fn: () => void): void {
  _nudge = fn;
}

export function setFocus(id: string | null): void {
  if (_focusedId === id) return;
  _focusedId = id;
  _nudge?.();
}

export function clearFocus(): void {
  setFocus(null);
}

export function toggleFocus(id: string): void {
  setFocus(_focusedId === id ? null : id);
}
