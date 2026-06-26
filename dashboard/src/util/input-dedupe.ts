/**
 * util/input-dedupe.ts — kill Lively's double-keystroke echo on text inputs.
 *
 * Lively's WebView2 wallpaper delivers each physical keystroke TWICE, so typing
 * "test" lands as "tteesstt" (same root cause as the terminal "llss" bug, which
 * is fixed with a 50ms onData dedupe). DOM text inputs need the same guard: an
 * identical character inserted again within a tiny window is the echo — drop it.
 *
 * A real human double-letter ("ll" in hello) is typed far slower than the ~0ms
 * echo, so a small window catches the echo without eating intentional repeats.
 */

const ECHO_WINDOW_MS = 50;

/** Pure decision: is this insert the duplicate echo of the previous one? */
export function isEchoKeystroke(
  prev: { data: string; t: number } | null,
  data: string,
  now: number,
  windowMs = ECHO_WINDOW_MS,
): boolean {
  return prev !== null && prev.data === data && (now - prev.t) < windowMs;
}

/**
 * Install one capturing `beforeinput` listener that de-echoes every text input
 * + textarea on the page (current and future). Call once at startup.
 */
export function installWallpaperInputDedupe(doc: Document = document): void {
  const last = new WeakMap<EventTarget, { data: string; t: number }>();
  doc.addEventListener(
    'beforeinput',
    (e) => {
      const ev = e as InputEvent;
      const t = ev.target;
      if (!(t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement)) return;
      if (ev.inputType !== 'insertText' || !ev.data) return;
      const now = performance.now();
      const prev = last.get(t) ?? null;
      if (isEchoKeystroke(prev, ev.data, now)) {
        ev.preventDefault();           // swallow the echoed character
        last.delete(t);                // reset so a 3rd real press isn't eaten
        return;
      }
      last.set(t, { data: ev.data, t: now });
    },
    true, // capture, so we run before the input applies the value
  );
}
