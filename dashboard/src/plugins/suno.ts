/**
 * plugins/suno.ts — Suno track-feed bridge (NOT a grid panel anymore).
 *
 * The Suno player moved to the top bar (transport) + a dropdown song navigator
 * (search / full list / scrub / volume) — see index.html #topbar-suno +
 * #suno-dropdown and the engine in ../panels/suno.ts. This module is now just
 * the bridge between the scheduler's /v1/suno/tracks poll (main.ts pollSuno)
 * and the player: it caches the latest list and feeds it to the player. No
 * PanelPlugin registration → the player no longer consumes a dashboard cell.
 */

import type { SunoTrack } from '../types.js';
import { updateSunoTracks } from '../panels/suno.js';

/** Most recent track list received from the scheduler poll. */
let _cachedTracks: SunoTrack[] = [];

/** Latest cached tracks (the player reads this on init if a poll beat it). */
export function cachedSunoTracks(): SunoTrack[] {
  return _cachedTracks;
}

/**
 * Receive a fresh track list from the scheduler poll and feed the player.
 * The top-bar player is initialized once at startup (main.ts), so we can
 * always push straight through.
 */
export function onSunoTracks(tracks: SunoTrack[]): void {
  _cachedTracks = tracks;
  updateSunoTracks(tracks);
}
