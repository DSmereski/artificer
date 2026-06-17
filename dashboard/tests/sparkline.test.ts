import { describe, it, expect } from 'vitest';
import { pushHist } from '../src/charts/sparkline.js';

describe('pushHist', () => {
  it('appends newest last', () => {
    const b: number[] = [];
    pushHist(b, 1); pushHist(b, 2); pushHist(b, 3);
    expect(b).toEqual([1, 2, 3]);
  });

  it('caps the buffer (drops oldest)', () => {
    const b: number[] = [];
    for (let i = 0; i < 60; i++) pushHist(b, i, 48);
    expect(b).toHaveLength(48);
    expect(b[0]).toBe(12);       // 0..11 dropped
    expect(b[b.length - 1]).toBe(59);
  });

  it('coerces non-finite to 0', () => {
    const b: number[] = [];
    pushHist(b, NaN); pushHist(b, Infinity);
    expect(b).toEqual([0, 0]);
  });
});
