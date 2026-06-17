/**
 * ws_frames.ts — Pure frame-shaping functions for WS events.
 *
 * Separated from ws.ts so unit tests can import these without pulling in
 * pause.ts (which references window.livelyWallpaperPlaybackChanged at
 * module level and fails in the Node/vitest environment).
 *
 * No DOM, no WebSocket, no imports with side effects.
 */

// ─── Event frame shapes ───────────────────────────────────────────────────────

export interface V1EventFrame {
  type: string;
  [key: string]: unknown;
}

export interface BoardEventFrame {
  type: string;
  slug?: string;
  status?: string;
  [key: string]: unknown;
}

export type TickerEvent = {
  label: string;
  css?: string;
};

// ─── shapeFrameToTicker ───────────────────────────────────────────────────────

/**
 * Shape a raw /v1/events frame into a human-readable ticker string.
 * Returns null for frame types that don't deserve a ticker entry.
 */
export function shapeFrameToTicker(frame: V1EventFrame): TickerEvent | null {
  const { type } = frame;

  switch (type) {
    case 'task_progress': {
      const slug  = String(frame['slug']  ?? '?');
      const turns = String(frame['turns'] ?? '?');
      return { label: `task ${slug} — turn ${turns}`, css: 'ticker-progress' };
    }
    case 'task_moved': {
      const slug   = String(frame['slug']   ?? '?');
      const status = String(frame['status'] ?? '?');
      return { label: `${slug} → ${status}`, css: 'ticker-moved' };
    }
    case 'escalation': {
      const reason = String(frame['reason'] ?? frame['title'] ?? 'escalation');
      return { label: `ESCALATION: ${reason}`, css: 'ticker-escalation' };
    }
    case 'chat': {
      const text  = String(frame['text'] ?? frame['message'] ?? '...');
      const short = text.length > 60 ? text.slice(0, 59) + '…' : text;
      return { label: short, css: 'ticker-chat' };
    }
    case 'image_done':
    case 'image-done': {
      const title = String(frame['title'] ?? frame['slug'] ?? 'image ready');
      return { label: `image done: ${title}`, css: 'ticker-image' };
    }
    case 'scout_alert': {
      const msg = String(frame['message'] ?? 'scout alert');
      return { label: `alert: ${msg}`, css: 'ticker-alert' };
    }
    default:
      return null;
  }
}

// ─── shapeBoardFrameToTicker ─────────────────────────────────────────────────

/**
 * Shape a /board/events frame into a ticker event.
 */
export function shapeBoardFrameToTicker(frame: BoardEventFrame): TickerEvent | null {
  const { type, slug, status } = frame;
  switch (type) {
    case 'task_moved':
      return { label: `board: ${slug ?? '?'} → ${status ?? '?'}`, css: 'ticker-moved' };
    case 'task_created':
      return { label: `new task: ${slug ?? '?'}`, css: 'ticker-progress' };
    case 'board_paused':
      return { label: 'board paused ⏸', css: 'ticker-alert' };
    case 'board_resumed':
      return { label: 'board resumed ▶', css: 'ticker-progress' };
    default:
      return null;
  }
}
