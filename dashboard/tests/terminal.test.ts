/**
 * tests/terminal.test.ts — Unit tests for the terminal plugin's pure logic.
 *
 * Tests the WS frame builders and fit-dimension calculator.
 * No DOM / WebSocket / xterm required — all pure functions.
 */

import { describe, it, expect } from 'vitest';
import {
  encodeInputFrame,
  buildResizeFrame,
  calcFitDimensions,
} from '../src/plugins/terminal.js';

// ─── encodeInputFrame ─────────────────────────────────────────────────────────

describe('encodeInputFrame', () => {
  it('produces a JSON object with type=input and a base64 data field', () => {
    const frame = encodeInputFrame('hello\r\n');
    const parsed = JSON.parse(frame) as { type: string; data: string };
    expect(parsed.type).toBe('input');
    expect(typeof parsed.data).toBe('string');
  });

  it('round-trips ASCII text through base64', () => {
    const original = 'Write-Host "Hello, Hive"\r\n';
    const frame = encodeInputFrame(original);
    const { data } = JSON.parse(frame) as { data: string };
    // atob is available in Node 18+ (globalThis.atob) and in browsers.
    const decoded = atob(data);
    expect(decoded).toBe(original);
  });

  it('handles empty string without throwing', () => {
    expect(() => encodeInputFrame('')).not.toThrow();
    const frame = encodeInputFrame('');
    const parsed = JSON.parse(frame) as { type: string; data: string };
    expect(parsed.type).toBe('input');
  });

  it('handles non-ASCII (emoji, unicode) without throwing', () => {
    expect(() => encodeInputFrame('👾')).not.toThrow();
    const frame = encodeInputFrame('🔥');
    const parsed = JSON.parse(frame) as { type: string; data: string };
    expect(parsed.type).toBe('input');
  });

  it('produces valid JSON', () => {
    const frame = encodeInputFrame('ls\r\n');
    expect(() => JSON.parse(frame)).not.toThrow();
  });
});

// ─── buildResizeFrame ─────────────────────────────────────────────────────────

describe('buildResizeFrame', () => {
  it('produces a JSON frame with type=resize and correct cols/rows', () => {
    const frame = buildResizeFrame(120, 40);
    const parsed = JSON.parse(frame) as { type: string; cols: number; rows: number };
    expect(parsed.type).toBe('resize');
    expect(parsed.cols).toBe(120);
    expect(parsed.rows).toBe(40);
  });

  it('preserves integer values exactly', () => {
    const frame = buildResizeFrame(80, 24);
    const parsed = JSON.parse(frame) as { cols: number; rows: number };
    expect(parsed.cols).toBe(80);
    expect(parsed.rows).toBe(24);
  });

  it('handles min dimensions (1x1)', () => {
    const frame = buildResizeFrame(1, 1);
    const parsed = JSON.parse(frame) as { cols: number; rows: number };
    expect(parsed.cols).toBe(1);
    expect(parsed.rows).toBe(1);
  });

  it('produces valid JSON', () => {
    expect(() => JSON.parse(buildResizeFrame(200, 50))).not.toThrow();
  });
});

// ─── calcFitDimensions ────────────────────────────────────────────────────────

describe('calcFitDimensions', () => {
  it('calculates correct cols and rows for standard 8x16 cells', () => {
    // 800 wide / 8 charW = 100 cols; 480 high / 16 charH = 30 rows
    const { cols, rows } = calcFitDimensions(800, 480, 8, 16);
    expect(cols).toBe(100);
    expect(rows).toBe(30);
  });

  it('floors fractional column/row count', () => {
    // 805 / 8 = 100.625 → 100
    const { cols } = calcFitDimensions(805, 480, 8, 16);
    expect(cols).toBe(100);
  });

  it('clamps cols to minimum 1', () => {
    const { cols } = calcFitDimensions(4, 16, 8, 16);
    expect(cols).toBe(1);
  });

  it('clamps rows to minimum 1', () => {
    const { rows } = calcFitDimensions(80, 8, 8, 16);
    expect(rows).toBe(1);
  });

  it('clamps cols to maximum 500', () => {
    const { cols } = calcFitDimensions(100_000, 100, 8, 16);
    expect(cols).toBe(500);
  });

  it('clamps rows to maximum 200', () => {
    const { rows } = calcFitDimensions(100, 100_000, 8, 16);
    expect(rows).toBe(200);
  });

  it('handles exact multiples correctly', () => {
    const { cols, rows } = calcFitDimensions(80, 24, 1, 1);
    expect(cols).toBe(80);
    expect(rows).toBe(24);
  });

  it('handles JetBrains Mono typical cell size', () => {
    // 13px font → approx 7.8 charW, 15.6 charH
    const { cols, rows } = calcFitDimensions(600, 390, 7.8, 15.6);
    expect(cols).toBe(76);  // floor(600 / 7.8)
    expect(rows).toBe(25);  // floor(390 / 15.6)
  });

  it('returns integer cols and rows always', () => {
    const { cols, rows } = calcFitDimensions(999, 777, 7.3, 14.7);
    expect(Number.isInteger(cols)).toBe(true);
    expect(Number.isInteger(rows)).toBe(true);
  });
});
