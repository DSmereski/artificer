/**
 * tests/tokens-day.test.ts — Unit tests for the tokensByDayToUplot
 * pure data-shaping function in src/plugins/tokens-day.ts.
 *
 * No DOM required — vitest runs in node environment.
 */

import { describe, it, expect } from 'vitest';
import { tokensByDayToUplot } from '../src/plugins/tokens-day.js';
import type { TokensByDayEntry } from '../src/gateway.js';

// ─── helpers ──────────────────────────────────────────────────────────────────

function entry(date: string, hive: number, claude: number): TokensByDayEntry {
  return { date, hive, claude, total: hive + claude };
}

// ─── tokensByDayToUplot ───────────────────────────────────────────────────────

describe('tokensByDayToUplot', () => {
  it('returns three parallel arrays of equal length', () => {
    const data = [
      entry('2026-06-01', 100, 50),
      entry('2026-06-02', 200, 80),
      entry('2026-06-03', 0,   0),
    ];
    const [ts, hive, claude] = tokensByDayToUplot(data);
    expect(ts).toHaveLength(3);
    expect(hive).toHaveLength(3);
    expect(claude).toHaveLength(3);
  });

  it('converts YYYY-MM-DD to UTC midnight epoch seconds', () => {
    const [ts] = tokensByDayToUplot([entry('2026-01-01', 0, 0)]);
    // 2026-01-01T00:00:00Z = 1767225600000 ms = 1767225600 s
    expect(ts[0]).toBe(1_767_225_600);
  });

  it('preserves hive and claude series values unchanged', () => {
    const data = [
      entry('2026-06-01', 1234, 567),
      entry('2026-06-02', 0,    999),
    ];
    const [, hive, claude] = tokensByDayToUplot(data);
    expect(hive).toEqual([1234, 0]);
    expect(claude).toEqual([567, 999]);
  });

  it('returns ascending timestamps for ascending input dates', () => {
    const data = [
      entry('2026-06-10', 10, 5),
      entry('2026-06-11', 20, 8),
      entry('2026-06-12', 30, 15),
    ];
    const [ts] = tokensByDayToUplot(data);
    for (let i = 1; i < ts.length; i++) {
      expect(ts[i]).toBeGreaterThan(ts[i - 1]);
    }
  });

  it('handles an empty input array', () => {
    const [ts, hive, claude] = tokensByDayToUplot([]);
    expect(ts).toHaveLength(0);
    expect(hive).toHaveLength(0);
    expect(claude).toHaveLength(0);
  });

  it('skips entries with invalid date strings', () => {
    const data = [
      entry('not-a-date', 100, 50),
      entry('2026-06-01', 200, 80),
    ];
    const [ts, hive, claude] = tokensByDayToUplot(data);
    expect(ts).toHaveLength(1);
    expect(hive[0]).toBe(200);
    expect(claude[0]).toBe(80);
  });

  it('handles zero token values without errors', () => {
    const data = Array.from({ length: 30 }, (_, i) => {
      const d = new Date(Date.UTC(2026, 4, 1 + i));
      const iso = d.toISOString().slice(0, 10);
      return entry(iso, 0, 0);
    });
    const [ts, hive, claude] = tokensByDayToUplot(data);
    expect(ts).toHaveLength(30);
    expect(hive.every(v => v === 0)).toBe(true);
    expect(claude.every(v => v === 0)).toBe(true);
  });

  it('handles large token values (M-scale) without overflow', () => {
    const data = [entry('2026-06-01', 5_000_000, 2_500_000)];
    const [, hive, claude] = tokensByDayToUplot(data);
    expect(hive[0]).toBe(5_000_000);
    expect(claude[0]).toBe(2_500_000);
  });

  it('produces timestamps as seconds not milliseconds', () => {
    // Unix timestamps in seconds are ~1.7e9; milliseconds would be ~1.7e12.
    const [ts] = tokensByDayToUplot([entry('2026-06-01', 0, 0)]);
    expect(ts[0]).toBeLessThan(2e10);   // definitely seconds
    expect(ts[0]).toBeGreaterThan(1e9); // sane epoch range
  });
});
