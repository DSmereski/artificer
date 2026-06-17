/**
 * tests/ws.test.ts — Unit tests for WS frame → ticker-event shaping.
 *
 * Pure tests on shapeFrameToTicker and shapeBoardFrameToTicker.
 * No WebSocket / DOM required.
 */

import { describe, it, expect } from 'vitest';
import {
  shapeFrameToTicker,
  shapeBoardFrameToTicker,
  type V1EventFrame,
  type BoardEventFrame,
} from '../src/ws_frames.js';

// ─── shapeFrameToTicker ──────────────────────────────────────────────────────

describe('shapeFrameToTicker', () => {
  it('returns null for unknown frame types', () => {
    const frame: V1EventFrame = { type: 'unknown_event' };
    expect(shapeFrameToTicker(frame)).toBeNull();
  });

  it('shapes task_progress frames', () => {
    const frame: V1EventFrame = { type: 'task_progress', slug: 'my-task', turns: 7 };
    const result = shapeFrameToTicker(frame);
    expect(result).not.toBeNull();
    expect(result!.label).toContain('my-task');
    expect(result!.label).toContain('7');
    expect(result!.css).toBe('ticker-progress');
  });

  it('shapes task_moved frames', () => {
    const frame: V1EventFrame = { type: 'task_moved', slug: 'build-thing', status: 'review' };
    const result = shapeFrameToTicker(frame);
    expect(result).not.toBeNull();
    expect(result!.label).toContain('build-thing');
    expect(result!.label).toContain('review');
    expect(result!.css).toBe('ticker-moved');
  });

  it('shapes escalation frames', () => {
    const frame: V1EventFrame = { type: 'escalation', reason: 'smoke failed' };
    const result = shapeFrameToTicker(frame);
    expect(result).not.toBeNull();
    expect(result!.label.toLowerCase()).toContain('escalation');
    expect(result!.label).toContain('smoke failed');
    expect(result!.css).toBe('ticker-escalation');
  });

  it('shapes escalation frames with title fallback', () => {
    const frame: V1EventFrame = { type: 'escalation', title: 'needs attention' };
    const result = shapeFrameToTicker(frame);
    expect(result).not.toBeNull();
    expect(result!.label).toContain('needs attention');
  });

  it('shapes chat frames and truncates long text', () => {
    const longText = 'A'.repeat(80);
    const frame: V1EventFrame = { type: 'chat', text: longText };
    const result = shapeFrameToTicker(frame);
    expect(result).not.toBeNull();
    expect(result!.label.length).toBeLessThanOrEqual(61); // 60 chars + ellipsis
    expect(result!.css).toBe('ticker-chat');
  });

  it('shapes short chat frames without truncation', () => {
    const frame: V1EventFrame = { type: 'chat', text: 'Hello world' };
    const result = shapeFrameToTicker(frame);
    expect(result!.label).toBe('Hello world');
  });

  it('shapes image_done frames', () => {
    const frame: V1EventFrame = { type: 'image_done', title: 'my-artwork' };
    const result = shapeFrameToTicker(frame);
    expect(result).not.toBeNull();
    expect(result!.label).toContain('my-artwork');
    expect(result!.css).toBe('ticker-image');
  });

  it('shapes image-done (hyphenated) frames', () => {
    const frame: V1EventFrame = { type: 'image-done', slug: 'art-slug' };
    const result = shapeFrameToTicker(frame);
    expect(result).not.toBeNull();
    expect(result!.label).toContain('art-slug');
  });

  it('shapes scout_alert frames', () => {
    const frame: V1EventFrame = { type: 'scout_alert', message: 'GPU temp high' };
    const result = shapeFrameToTicker(frame);
    expect(result).not.toBeNull();
    expect(result!.label).toContain('GPU temp high');
    expect(result!.css).toBe('ticker-alert');
  });

  it('uses ? placeholder for missing slug/turns in task_progress', () => {
    const frame: V1EventFrame = { type: 'task_progress' };
    const result = shapeFrameToTicker(frame);
    expect(result).not.toBeNull();
    expect(result!.label).toContain('?');
  });
});

// ─── shapeBoardFrameToTicker ─────────────────────────────────────────────────

describe('shapeBoardFrameToTicker', () => {
  it('returns null for unknown board events', () => {
    const frame: BoardEventFrame = { type: 'project_created' };
    expect(shapeBoardFrameToTicker(frame)).toBeNull();
  });

  it('shapes task_moved board events', () => {
    const frame: BoardEventFrame = { type: 'task_moved', slug: 'impl-auth', status: 'done' };
    const result = shapeBoardFrameToTicker(frame);
    expect(result).not.toBeNull();
    expect(result!.label).toContain('impl-auth');
    expect(result!.label).toContain('done');
    expect(result!.css).toBe('ticker-moved');
  });

  it('shapes task_created board events', () => {
    const frame: BoardEventFrame = { type: 'task_created', slug: 'new-task' };
    const result = shapeBoardFrameToTicker(frame);
    expect(result).not.toBeNull();
    expect(result!.label).toContain('new-task');
    expect(result!.css).toBe('ticker-progress');
  });

  it('shapes board_paused events', () => {
    const frame: BoardEventFrame = { type: 'board_paused' };
    const result = shapeBoardFrameToTicker(frame);
    expect(result).not.toBeNull();
    expect(result!.label.toLowerCase()).toContain('paused');
    expect(result!.css).toBe('ticker-alert');
  });

  it('shapes board_resumed events', () => {
    const frame: BoardEventFrame = { type: 'board_resumed' };
    const result = shapeBoardFrameToTicker(frame);
    expect(result).not.toBeNull();
    expect(result!.label.toLowerCase()).toContain('resumed');
    expect(result!.css).toBe('ticker-progress');
  });

  it('uses ? for missing slug in task_moved', () => {
    const frame: BoardEventFrame = { type: 'task_moved' };
    const result = shapeBoardFrameToTicker(frame);
    expect(result!.label).toContain('?');
  });
});
