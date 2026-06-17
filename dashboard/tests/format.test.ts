/**
 * tests/format.test.ts — Unit tests for src/format.ts
 *
 * Pure formatting functions + rolling buffer logic — no DOM required.
 */

import { describe, it, expect } from 'vitest';
import {
  fmtNum,
  fmtCost,
  fmtPct,
  fmtRate,
  fmtDuration,
  fmtTime,
  fmtRelative,
  gpuLabel,
  pushSample,
  bufferToUplot,
  throughputPerHour,
  escHtml,
} from '../src/format.js';
import type { BoardStatsSample } from '../src/types.js';

// ─── fmtNum ───────────────────────────────────────────────────────────────────

describe('fmtNum', () => {
  it('formats small integers verbatim', () => {
    expect(fmtNum(0)).toBe('0');
    expect(fmtNum(999)).toBe('999');
  });

  it('formats thousands with k suffix', () => {
    expect(fmtNum(1_000)).toBe('1.0k');
    expect(fmtNum(1_234)).toBe('1.2k');
    expect(fmtNum(999_999)).toBe('1000.0k');
  });

  it('formats millions with M suffix', () => {
    expect(fmtNum(1_000_000)).toBe('1.0M');
    expect(fmtNum(2_500_000)).toBe('2.5M');
  });

  it('handles non-finite values', () => {
    expect(fmtNum(NaN)).toBe('--');
    expect(fmtNum(Infinity)).toBe('--');
  });
});

// ─── fmtCost ──────────────────────────────────────────────────────────────────

describe('fmtCost', () => {
  it('formats zero', () => {
    expect(fmtCost(0)).toBe('$0.00');
  });

  it('formats typical cost', () => {
    expect(fmtCost(12.5678)).toBe('$12.57');
  });

  it('formats null/undefined as --', () => {
    expect(fmtCost(null)).toBe('--');
    expect(fmtCost(undefined)).toBe('--');
  });

  it('formats NaN as --', () => {
    expect(fmtCost(NaN)).toBe('--');
  });
});

// ─── fmtPct ───────────────────────────────────────────────────────────────────

describe('fmtPct', () => {
  it('rounds and appends %', () => {
    expect(fmtPct(87.4)).toBe('87%');
    expect(fmtPct(100)).toBe('100%');
    expect(fmtPct(0)).toBe('0%');
  });

  it('handles null/undefined', () => {
    expect(fmtPct(null)).toBe('--%');
    expect(fmtPct(undefined)).toBe('--%');
  });
});

// ─── fmtRate ─────────────────────────────────────────────────────────────────

describe('fmtRate', () => {
  it('converts 0-1 to percentage string', () => {
    expect(fmtRate(0.034)).toBe('3.4%');
    expect(fmtRate(1)).toBe('100.0%');
    expect(fmtRate(0)).toBe('0.0%');
  });

  it('handles null/undefined', () => {
    expect(fmtRate(null)).toBe('--%');
  });
});

// ─── fmtDuration ─────────────────────────────────────────────────────────────

describe('fmtDuration', () => {
  it('formats seconds', () => {
    expect(fmtDuration(0)).toBe('0s');
    expect(fmtDuration(45)).toBe('45s');
  });

  it('formats minutes', () => {
    expect(fmtDuration(61)).toBe('1m 1s');
    expect(fmtDuration(120)).toBe('2m 0s');
  });

  it('formats hours', () => {
    expect(fmtDuration(3665)).toBe('1h 1m');
    expect(fmtDuration(90000)).toBe('25h 0m');
  });

  it('handles null/undefined/negative', () => {
    expect(fmtDuration(null)).toBe('--');
    expect(fmtDuration(undefined)).toBe('--');
    expect(fmtDuration(-1)).toBe('--');
  });
});

// ─── fmtTime ─────────────────────────────────────────────────────────────────

describe('fmtTime', () => {
  it('returns -- for null/empty', () => {
    expect(fmtTime(null)).toBe('--');
    expect(fmtTime(undefined)).toBe('--');
    expect(fmtTime('')).toBe('--');
  });

  it('returns -- for invalid ISO', () => {
    expect(fmtTime('not-a-date')).toBe('--');
  });

  it('parses a valid ISO string to HH:MM', () => {
    // Use a known UTC time — locale-independent check
    const result = fmtTime('2026-06-13T14:23:00Z');
    // Just verify it looks like HH:MM (locale may shift hour)
    expect(result).toMatch(/^\d{2}:\d{2}$/);
  });
});

// ─── fmtRelative ─────────────────────────────────────────────────────────────

describe('fmtRelative', () => {
  it('returns just now for very recent', () => {
    expect(fmtRelative(Date.now() + 1000)).toBe('just now');
  });

  it('formats seconds ago', () => {
    const result = fmtRelative(Date.now() - 30_000);
    expect(result).toMatch(/^\d+s ago$/);
  });

  it('formats minutes ago', () => {
    const result = fmtRelative(Date.now() - 70_000);
    expect(result).toBe('1m ago');
  });

  it('formats hours ago', () => {
    const result = fmtRelative(Date.now() - 3_700_000);
    expect(result).toBe('1h ago');
  });

  it('handles null', () => {
    expect(fmtRelative(null)).toBe('--');
  });
});

// ─── gpuLabel ────────────────────────────────────────────────────────────────

describe('gpuLabel', () => {
  it('labels 5060 Ti cards', () => {
    expect(gpuLabel('NVIDIA GeForce RTX 5060 Ti', 0)).toBe('GPU 1 — RTX 5060 Ti');
    expect(gpuLabel('NVIDIA GeForce RTX 5060 Ti', 1)).toBe('GPU 2 — RTX 5060 Ti');
  });

  it('labels the 4080 as gaming', () => {
    expect(gpuLabel('NVIDIA GeForce RTX 4080', 2)).toBe('RTX 4080 (gaming)');
  });

  it('falls back for unknown GPUs', () => {
    expect(gpuLabel('NVIDIA Tesla V100', 3)).toBe('GPU 4 — NVIDIA Tesla V100');
  });
});

// ─── pushSample ──────────────────────────────────────────────────────────────

describe('pushSample', () => {
  const makeSample = (ts: number, done = 0): BoardStatsSample => ({
    ts,
    hive_tokens:    1000,
    claude_tokens:  500,
    cost_usd:       0.5,
    done_count:     done,
    smoke_pass_pct: 100,
    parse_fail_rate: 0,
  });

  it('appends a sample to an empty buffer', () => {
    const buf = pushSample([], makeSample(1000));
    expect(buf).toHaveLength(1);
    expect(buf[0].ts).toBe(1000);
  });

  it('does not mutate the original buffer', () => {
    const original: BoardStatsSample[] = [makeSample(1000)];
    const next = pushSample(original, makeSample(2000));
    expect(original).toHaveLength(1); // unchanged
    expect(next).toHaveLength(2);
  });

  it('caps at 120 samples', () => {
    let buf: BoardStatsSample[] = [];
    for (let i = 0; i < 130; i++) {
      buf = pushSample(buf, makeSample(i * 1000));
    }
    expect(buf).toHaveLength(120);
    // Should keep the most recent 120
    expect(buf[0].ts).toBe(10_000);
    expect(buf[119].ts).toBe(129_000);
  });
});

// ─── bufferToUplot ───────────────────────────────────────────────────────────

describe('bufferToUplot', () => {
  const makeSample = (ts: number, cost: number): BoardStatsSample => ({
    ts,
    hive_tokens: 0, claude_tokens: 0,
    cost_usd: cost,
    done_count: 0, smoke_pass_pct: 0, parse_fail_rate: 0,
  });

  it('converts buffer to parallel timestamp/value arrays', () => {
    const buf = [makeSample(10_000, 1.0), makeSample(20_000, 2.0)];
    const [ts, vals] = bufferToUplot(buf, (s) => s.cost_usd);
    expect(ts).toEqual([10, 20]);  // ms → seconds
    expect(vals).toEqual([1.0, 2.0]);
  });

  it('returns empty arrays for empty buffer', () => {
    const [ts, vals] = bufferToUplot([], (s) => s.cost_usd);
    expect(ts).toHaveLength(0);
    expect(vals).toHaveLength(0);
  });
});

// ─── throughputPerHour ───────────────────────────────────────────────────────

describe('throughputPerHour', () => {
  const makeSample = (ts: number, done: number): BoardStatsSample => ({
    ts,
    hive_tokens: 0, claude_tokens: 0, cost_usd: 0,
    done_count: done, smoke_pass_pct: 0, parse_fail_rate: 0,
  });

  it('returns 0 for fewer than 2 samples', () => {
    expect(throughputPerHour([])).toBe(0);
    expect(throughputPerHour([makeSample(0, 5)])).toBe(0);
  });

  it('calculates correct done/hour', () => {
    // 6 done tasks over 1 hour = 6/h
    const buf = [
      makeSample(0,           0),
      makeSample(3_600_000,   6),
    ];
    expect(throughputPerHour(buf)).toBeCloseTo(6, 1);
  });

  it('does not go negative', () => {
    const buf = [
      makeSample(0,         10),
      makeSample(3_600_000,  5), // done count went down (shouldn't happen, but guard)
    ];
    expect(throughputPerHour(buf)).toBeGreaterThanOrEqual(0);
  });
});

// ─── escHtml ─────────────────────────────────────────────────────────────────

describe('escHtml', () => {
  it('escapes all HTML special characters', () => {
    expect(escHtml('<script>alert("xss")</script>')).toBe(
      '&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;'
    );
  });

  it('escapes ampersands', () => {
    expect(escHtml('A & B')).toBe('A &amp; B');
  });

  it('escapes single quotes', () => {
    expect(escHtml("it's")).toBe("it&#39;s");
  });

  it('leaves normal text unchanged', () => {
    expect(escHtml('hello world')).toBe('hello world');
  });
});
