/**
 * tests/suno.test.ts — Unit tests for suno player pure logic.
 *
 * Tests shapeTracks, fmtTrackDuration, and nextIndex — all pure functions
 * with no DOM or audio dependencies.
 */

import { describe, it, expect } from 'vitest';
import {
  fmtTrackDuration,
  filterTracks,
  nextIndex,
  shapeTracks,
  type PlayerTrack,
} from '../src/panels/suno.js';
import type { SunoTrack } from '../src/types.js';

// ─── filterTracks ─────────────────────────────────────────────────────────────

function _pt(title: string, artist = 'Operator'): PlayerTrack {
  return {
    id: title,
    title,
    artist,
    durationFmt: '1:00',
    imageUrl: null,
    audioUrl: `/audio/${title}`,
  };
}

describe('filterTracks', () => {
  const tracks: PlayerTrack[] = [
    _pt('Velvet Crown'),
    _pt('Tokyo Drift', 'PuS'),
    _pt('Purple Haze (Mashup)'),
  ];

  it('returns the whole list with index pairs for an empty query', () => {
    const r = filterTracks(tracks, '');
    expect(r).toHaveLength(3);
    expect(r.map(([i]) => i)).toEqual([0, 1, 2]);
  });

  it('treats whitespace-only query as empty', () => {
    expect(filterTracks(tracks, '   ')).toHaveLength(3);
  });

  it('matches on title, case-insensitively', () => {
    const r = filterTracks(tracks, 'velvet');
    expect(r).toHaveLength(1);
    expect(r[0]![1].title).toBe('Velvet Crown');
  });

  it('matches on artist', () => {
    const r = filterTracks(tracks, 'pus');
    expect(r).toHaveLength(1);
    expect(r[0]![1].title).toBe('Tokyo Drift');
  });

  it('preserves absolute indices after filtering', () => {
    const r = filterTracks(tracks, 'purple');
    expect(r).toHaveLength(1);
    expect(r[0]![0]).toBe(2); // absolute index in the full list
  });

  it('returns empty for no match', () => {
    expect(filterTracks(tracks, 'zzzznope')).toEqual([]);
  });
});

// ─── fmtTrackDuration ─────────────────────────────────────────────────────────

describe('fmtTrackDuration', () => {
  it('formats zero seconds', () => {
    expect(fmtTrackDuration(0)).toBe('0:00');
  });

  it('formats sub-minute durations', () => {
    expect(fmtTrackDuration(45)).toBe('0:45');
    expect(fmtTrackDuration(9)).toBe('0:09');
  });

  it('formats exactly one minute', () => {
    expect(fmtTrackDuration(60)).toBe('1:00');
  });

  it('formats multi-minute durations', () => {
    expect(fmtTrackDuration(73.4)).toBe('1:13');
    expect(fmtTrackDuration(180)).toBe('3:00');
    expect(fmtTrackDuration(254.48)).toBe('4:14');
  });

  it('pads seconds with leading zero', () => {
    expect(fmtTrackDuration(61)).toBe('1:01');
    expect(fmtTrackDuration(70)).toBe('1:10');
  });

  it('returns --:-- for null', () => {
    expect(fmtTrackDuration(null)).toBe('--:--');
  });

  it('returns --:-- for undefined', () => {
    expect(fmtTrackDuration(undefined)).toBe('--:--');
  });

  it('returns --:-- for negative numbers', () => {
    expect(fmtTrackDuration(-5)).toBe('--:--');
  });

  it('returns --:-- for NaN', () => {
    expect(fmtTrackDuration(NaN)).toBe('--:--');
  });
});

// ─── nextIndex ────────────────────────────────────────────────────────────────

describe('nextIndex', () => {
  it('advances to next track in sequence', () => {
    expect(nextIndex(0, 5, 'next', false)).toBe(1);
    expect(nextIndex(3, 5, 'next', false)).toBe(4);
  });

  it('wraps from last to first on next', () => {
    expect(nextIndex(4, 5, 'next', false)).toBe(0);
  });

  it('goes back on prev', () => {
    expect(nextIndex(3, 5, 'prev', false)).toBe(2);
    expect(nextIndex(1, 5, 'prev', false)).toBe(0);
  });

  it('wraps from first to last on prev', () => {
    expect(nextIndex(0, 5, 'prev', false)).toBe(4);
  });

  it('handles single-track list without infinite loop', () => {
    expect(nextIndex(0, 1, 'next', false)).toBe(0);
    expect(nextIndex(0, 1, 'prev', false)).toBe(0);
    expect(nextIndex(0, 1, 'next', true)).toBe(0);
  });

  it('shuffle returns a valid index', () => {
    for (let i = 0; i < 20; i++) {
      const idx = nextIndex(2, 5, 'next', true);
      expect(idx).toBeGreaterThanOrEqual(0);
      expect(idx).toBeLessThan(5);
    }
  });

  it('shuffle never returns current index when list > 1', () => {
    const current = 2;
    for (let i = 0; i < 50; i++) {
      const idx = nextIndex(current, 5, 'next', true);
      expect(idx).not.toBe(current);
    }
  });

  it('handles empty list gracefully', () => {
    expect(nextIndex(0, 0, 'next', false)).toBe(0);
  });
});

// ─── shapeTracks ─────────────────────────────────────────────────────────────

describe('shapeTracks', () => {
  const rawTrack: SunoTrack = {
    id:          'aabbccdd-1122-3344-5566-778899aabbcc',
    title:       'Velvet Crown',
    artist_name: 'Penguin',
    tags:        'deathcore phonk',
    duration:    254.48,
    image_url:   'https://cdn.suno.ai/image_test.jpeg',
    play_count:  3,
  };

  it('shapes a track correctly', () => {
    const [t] = shapeTracks([rawTrack]);
    expect(t.id).toBe(rawTrack.id);
    expect(t.title).toBe('Velvet Crown');
    expect(t.artist).toBe('Penguin');
    expect(t.durationFmt).toBe('4:14');
    expect(t.imageUrl).toBe('https://cdn.suno.ai/image_test.jpeg');
    expect(t.audioUrl).toContain(rawTrack.id);
  });

  it('returns empty array for empty input', () => {
    expect(shapeTracks([])).toEqual([]);
  });

  it('uses Unknown for missing title and artist', () => {
    const bare: SunoTrack = {
      id:          '00000000-0000-0000-0000-000000000000',
      title:       '',
      artist_name: '',
      tags:        null,
      duration:    null,
      image_url:   null,
      play_count:  0,
    };
    const [t] = shapeTracks([bare]);
    expect(t.title).toBe('Unknown');
    expect(t.artist).toBe('Unknown');
    expect(t.durationFmt).toBe('--:--');
    expect(t.imageUrl).toBeNull();
  });

  it('builds the audio URL from the track id', () => {
    const [t] = shapeTracks([rawTrack]);
    expect(t.audioUrl).toMatch(/\/v1\/suno\/audio\/aabbccdd-1122-3344-5566-778899aabbcc/);
  });
});
