/**
 * pause.ts — Lively Wallpaper pause/resume integration.
 *
 * Lively calls `livelyWallpaperPlaybackChanged({IsPaused: bool})` when the
 * wallpaper is paused (fullscreen game detected) or resumed.
 *
 * NOTE: We intentionally do NOT listen to `document.visibilitychange` here.
 * WebView2 reports the page as "hidden" whenever ANY other window has focus
 * (even with AppFocusPause=0), so a visibilitychange listener would fire
 * applyPauseState(true) on every focus-away — suspending the scheduler and
 * stopping audio polls while a fullscreen-game pause is NOT in effect.
 * Lively's livelyWallpaperPlaybackChanged is the sole authority for pause state.
 *
 * When paused:
 *   - All poll timers are suspended (callers register via onPauseStateChange)
 *   - The PAUSED badge becomes visible
 *   - GPU load from the wallpaper drops to near-zero
 *
 * When resumed:
 *   - Timers restart + immediate refetch is requested
 */

type PauseListener = (isPaused: boolean) => void;

const listeners: PauseListener[] = [];
let _isPaused = false;

export function isPaused(): boolean {
  return _isPaused;
}

export function onPauseStateChange(fn: PauseListener): void {
  listeners.push(fn);
}

function applyPauseState(paused: boolean): void {
  if (_isPaused === paused) return;
  _isPaused = paused;

  const badge = document.getElementById('paused-badge');
  if (badge) {
    badge.classList.toggle('visible', paused);
  }

  for (const fn of listeners) {
    try {
      fn(paused);
    } catch (err) {
      console.error('[pause] listener error', err);
    }
  }

  console.info(`[pause] wallpaper ${paused ? 'PAUSED' : 'RESUMED'}`);
}

// ─── Lively hook (global function) ───────────────────────────────────────────

// Lively calls this as a plain function on the window object.
// We declare it on `window` so TypeScript doesn't complain.
declare global {
  interface Window {
    livelyWallpaperPlaybackChanged: (
      data: { IsPaused: boolean } | boolean,
    ) => void;
  }
}

window.livelyWallpaperPlaybackChanged = (data) => {
  const paused =
    typeof data === 'boolean' ? data : Boolean(data?.IsPaused);
  applyPauseState(paused);
};

// ─── document.hidden fallback ─────────────────────────────────────────────────
// Intentionally omitted: visibilitychange fires on every focus-away in WebView2
// (even when AppFocusPause=0 keeps the process alive), which would spuriously
// pause audio and polls while the music is supposed to keep playing.
// livelyWallpaperPlaybackChanged above is the sole pause trigger.
