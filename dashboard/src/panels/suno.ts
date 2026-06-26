/**
 * panels/suno.ts — Suno music player panel.
 *
 * Renders:
 *  - Now-playing card: album art, title, artist, scrub bar.
 *  - Transport controls: prev / play-pause / next / shuffle.
 *  - Scrollable track list: title, artist, duration; click to play.
 *
 * Uses a single HTML5 <audio> element. No autoplay on load. Persists
 * last-played track ID and volume to localStorage.
 *
 * Pure logic (track ordering, index navigation, duration formatting) is
 * exported for unit testing without a DOM.
 */

import type { SunoTrack } from '../types.js';
import { escHtml } from '../format.js';
import { sunoAudioUrl } from '../gateway.js';
import {
  getActiveSource,
  localTogglePlay,
  localSkip,
  setMusicAudio,
  initMusicPlayer,
} from './music.js';

// ─── Storage keys ─────────────────────────────────────────────────────────────

const LS_LAST_TRACK  = 'suno:lastTrackId';
const LS_VOLUME      = 'suno:volume';

// ─── Pure helpers (exported for unit tests) ───────────────────────────────────

/**
 * Shape a raw SunoTrack into the minimal object the player needs.
 * Duration is formatted as mm:ss.
 */
export interface PlayerTrack {
  id: string;
  title: string;
  artist: string;
  durationFmt: string;
  imageUrl: string | null;
  audioUrl: string;
}

export function shapeTracks(raw: SunoTrack[]): PlayerTrack[] {
  return raw.map((t) => ({
    id:          t.id,
    title:       t.title || 'Unknown',
    artist:      t.artist_name || 'Unknown',
    durationFmt: fmtTrackDuration(t.duration),
    imageUrl:    t.image_url ?? null,
    audioUrl:    sunoAudioUrl(t.id),
  }));
}

/**
 * Format a duration in seconds as mm:ss.
 *   fmtTrackDuration(73.4)  → "1:13"
 *   fmtTrackDuration(null)  → "--:--"
 */
export function fmtTrackDuration(seconds: number | null | undefined): string {
  if (seconds == null || !isFinite(seconds) || seconds < 0) return '--:--';
  const total = Math.floor(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

/**
 * Filter tracks by a free-text query (case-insensitive, matches title OR
 * artist). Returns pairs of [absoluteIndex, track] so the player can still
 * address the track by its real index in the full list after filtering.
 * Empty/whitespace query returns the whole list.
 */
export function filterTracks(
  tracks: PlayerTrack[],
  query: string,
): Array<[number, PlayerTrack]> {
  const q = query.trim().toLowerCase();
  const all: Array<[number, PlayerTrack]> = tracks.map((t, i) => [i, t]);
  if (!q) return all;
  return all.filter(
    ([, t]) =>
      t.title.toLowerCase().includes(q) || t.artist.toLowerCase().includes(q),
  );
}

/**
 * Compute the next track index given current index, direction, and shuffle.
 * Returns a new index in [0, length).
 */
export function nextIndex(
  current: number,
  length: number,
  direction: 'next' | 'prev',
  shuffle: boolean,
): number {
  if (length === 0) return 0;
  if (shuffle) {
    // Pick a random index different from current (if list > 1).
    if (length === 1) return 0;
    let idx = Math.floor(Math.random() * (length - 1));
    if (idx >= current) idx += 1;
    return idx;
  }
  if (direction === 'next') return (current + 1) % length;
  return (current - 1 + length) % length;
}

// ─── Player state ─────────────────────────────────────────────────────────────

interface PlayerState {
  tracks: PlayerTrack[];
  currentIndex: number;
  playing: boolean;
  shuffle: boolean;
  filter: string;
}

const _state: PlayerState = {
  tracks:       [],
  currentIndex: 0,
  playing:      false,
  shuffle:      false,
  filter:       '',
};

// ─── Track-list filter (set from the panel search box) ────────────────────────

export function getSunoFilter(): string {
  return _state.filter;
}

export function setSunoFilter(query: string): void {
  _state.filter = query;
  _renderTrackList();
}

let _audio: HTMLAudioElement | null = null;

// ─── DOM helpers ──────────────────────────────────────────────────────────────

function $<T extends HTMLElement>(id: string): T | null {
  return document.getElementById(id) as T | null;
}

// ─── Init ─────────────────────────────────────────────────────────────────────

/** Expose the shared audio element so music.ts can drive local playback. */
export function getSunoAudio(): HTMLAudioElement | null {
  return _audio;
}

export function initSunoPlayer(): void {
  _audio = new Audio();
  _audio.preload = 'none';
  // Enable the WebAudio analyser (visualizer). The gateway serves the audio
  // with Access-Control-Allow-Origin: * so anonymous CORS works; if it ever
  // didn't, the element just stays un-analysable (audio still plays).
  try { _audio.crossOrigin = 'anonymous'; } catch { /* ignore */ }

  // Restore volume
  const savedVol = localStorage.getItem(LS_VOLUME);
  _audio.volume = savedVol != null ? parseFloat(savedVol) : 0.8;

  // Share the audio element with the local music player.
  setMusicAudio(_audio);

  // Wire transport controls — delegate to local source when active.
  $('suno-btn-play')?.addEventListener('click',    () => {
    if (getActiveSource() === 'local') { localTogglePlay(); return; }
    _togglePlay();
  });
  $('suno-btn-prev')?.addEventListener('click',    () => {
    if (getActiveSource() === 'local') { localSkip('prev'); return; }
    _skipTrack('prev');
  });
  $('suno-btn-next')?.addEventListener('click',    () => {
    if (getActiveSource() === 'local') { localSkip('next'); return; }
    _skipTrack('next');
  });
  $('suno-btn-shuffle')?.addEventListener('click', () => _toggleShuffle());

  // Wire scrub bar
  const scrub = $<HTMLInputElement>('suno-scrub');
  if (scrub) {
    scrub.addEventListener('input', () => {
      if (_audio && isFinite(_audio.duration)) {
        _audio.currentTime = (parseFloat(scrub.value) / 1000) * _audio.duration;
      }
    });
  }

  // Wire volume
  const vol = $<HTMLInputElement>('suno-volume');
  if (vol) {
    vol.value = String(Math.round((_audio?.volume ?? 0.8) * 100));
    vol.addEventListener('input', () => {
      const v = parseFloat(vol.value) / 100;
      if (_audio) _audio.volume = v;
      localStorage.setItem(LS_VOLUME, String(v));
    });
  }

  // Audio events
  if (_audio) {
    _audio.addEventListener('timeupdate',  _onTimeUpdate);
    _audio.addEventListener('ended',       () => _skipTrack('next'));
    _audio.addEventListener('play',        () => _setPlayingUI(true));
    _audio.addEventListener('pause',       () => _setPlayingUI(false));
    _audio.addEventListener('error',       () => _setPlayingUI(false));
  }

  _wireDropdown();

  // F4: Init local music player (shares this <audio> element).
  initMusicPlayer();
}

// ─── Ducking (CC2 alerts lower the music briefly) ────────────────────────────

let _duckTimer: ReturnType<typeof setTimeout> | null = null;

/** Briefly lower Suno volume (e.g. while an alert stinger plays), then restore. */
export function duckSuno(ms = 1800): void {
  if (!_audio) return;
  const restore = _audio.volume;
  _audio.volume = Math.min(restore, restore * 0.25);
  if (_duckTimer) clearTimeout(_duckTimer);
  _duckTimer = setTimeout(() => {
    if (_audio) _audio.volume = restore;
    _duckTimer = null;
  }, ms);
}

// ─── Now-playing visualizer (it5 — WebAudio analyser, side-tap) ───────────────

let _audioCtx: AudioContext | null = null;
let _analyser: AnalyserNode | null = null;
let _vizRaf: number | null = null;
let _vizCanvas: HTMLCanvasElement | null = null;

/**
 * Expose the shared AnalyserNode so external layers (e.g. audio-viz-bg.ts)
 * can read frequency data from the same node without creating a second
 * MediaElementSource (which would throw InvalidStateError).
 *
 * Returns null until the first play event triggers _ensureAnalyser().
 */
export function getMusicAnalyser(): AnalyserNode | null {
  return _analyser;
}

/** Build the analyser graph once. Side-tap: source→destination keeps audio
 *  intact; source→analyser is a parallel tap that never reaches output, so a
 *  visualizer failure can't silence playback. */
function _ensureAnalyser(): boolean {
  if (_analyser) return true;
  if (!_audio || typeof AudioContext === 'undefined') return false;
  try {
    _audioCtx = new AudioContext();
    const src = _audioCtx.createMediaElementSource(_audio);
    _analyser = _audioCtx.createAnalyser();
    _analyser.fftSize = 128;
    src.connect(_audioCtx.destination); // audio path first (safe)
    src.connect(_analyser);             // parallel tap (no onward connect)
    return true;
  } catch {
    _analyser = null;
    return false;
  }
}

function _ddOpen(): boolean {
  const dd = $('suno-dropdown');
  return !!dd && !dd.hidden;
}

function _drawViz(): void {
  if (!_analyser || !_vizCanvas) return;
  const g = _vizCanvas.getContext('2d');
  if (!g) return;
  const n = _analyser.frequencyBinCount;
  const data = new Uint8Array(n);
  _analyser.getByteFrequencyData(data);
  const W = _vizCanvas.width, H = _vizCanvas.height;
  g.clearRect(0, 0, W, H);
  const bars = 56;
  const bw = W / bars;
  // Resolve copper + amber from CSS vars so bars recolor with the theme.
  const cs      = getComputedStyle(document.documentElement);
  const copperH = cs.getPropertyValue('--hex-copper').trim() || '#c07840';
  const amberH  = cs.getPropertyValue('--hex-amber').trim()  || '#e0a030';
  // Parse hex channels for interpolation.
  function _ch(hex: string, off: number) { return parseInt(hex.slice(off, off + 2), 16); }
  const cr = _ch(copperH, 1), cg = _ch(copperH, 3), cb = _ch(copperH, 5);
  const ar = _ch(amberH,  1), ag = _ch(amberH,  3), ab = _ch(amberH,  5);

  for (let i = 0; i < bars; i++) {
    const v = (data[Math.floor((i / bars) * n)] ?? 0) / 255;
    const bh = Math.max(1, v * H);
    // copper → amber by intensity, glow on the loud bars.
    const r = Math.round(cr + (ar - cr) * v);
    const gv = Math.round(cg + (ag - cg) * v);
    const b  = Math.round(cb + (ab - cb) * v);
    g.fillStyle = `rgba(${r},${gv},${b},${0.45 + v * 0.55})`;
    g.fillRect(i * bw, H - bh, Math.max(1, bw - 1), bh);
  }
}

function _vizTick(): void {
  _drawViz();
  _vizRaf = requestAnimationFrame(_vizTick);
}

function _startViz(): void {
  if (_vizRaf != null) return;
  if (!_vizCanvas) _vizCanvas = $('suno-viz') as HTMLCanvasElement | null;
  if (!_ensureAnalyser()) return;
  void _audioCtx?.resume?.();
  _vizTick();
}

function _stopViz(): void {
  if (_vizRaf != null) { cancelAnimationFrame(_vizRaf); _vizRaf = null; }
  if (_vizCanvas) {
    const g = _vizCanvas.getContext('2d');
    g?.clearRect(0, 0, _vizCanvas.width, _vizCanvas.height);
  }
}

/** Run the visualizer only when the dropdown is open AND audio is playing. */
function _syncViz(): void {
  if (_ddOpen() && _state.playing) _startViz();
  else _stopViz();
}

// ─── Song-navigator dropdown (top-bar player) ─────────────────────────────────

function _setDropdown(open: boolean): void {
  const dd = $('suno-dropdown');
  const toggle = $('suno-dd-toggle');
  if (!dd) return;
  dd.hidden = !open;
  toggle?.setAttribute('aria-expanded', String(open));
  _syncViz();
}

function _wireDropdown(): void {
  const toggle = $('suno-dd-toggle');
  const dd = $('suno-dropdown');
  if (!toggle || !dd) return;

  toggle.addEventListener('click', (e) => {
    e.stopPropagation();
    _setDropdown(dd.hidden); // hidden → open
  });

  // Close on outside click + Esc. Clicks inside the dropdown or on the
  // toggle don't close it (so search/scrub/list stay usable).
  document.addEventListener('click', (e) => {
    if (dd.hidden) return;
    const t = e.target as Node | null;
    if (t && (dd.contains(t) || toggle.contains(t))) return;
    _setDropdown(false);
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !dd.hidden) _setDropdown(false);
  });
}

// ─── Load / update tracks ─────────────────────────────────────────────────────

export function updateSunoTracks(raw: SunoTrack[]): void {
  const shaped = shapeTracks(raw);

  // Preserve current track if it's still in the list.
  const prevId = _state.tracks[_state.currentIndex]?.id;
  _state.tracks = shaped;

  if (prevId) {
    const idx = shaped.findIndex((t) => t.id === prevId);
    _state.currentIndex = idx >= 0 ? idx : 0;
  } else {
    // Try to restore last-played from localStorage.
    const savedId = localStorage.getItem(LS_LAST_TRACK);
    if (savedId) {
      const idx = shaped.findIndex((t) => t.id === savedId);
      _state.currentIndex = idx >= 0 ? idx : 0;
    } else {
      _state.currentIndex = 0;
    }
  }

  _renderTrackList();
  _renderNowPlaying();
}

// ─── Playback control ─────────────────────────────────────────────────────────

function _loadTrack(index: number, autoplay = false): void {
  if (!_audio || _state.tracks.length === 0) return;
  const track = _state.tracks[index];
  if (!track) return;

  _state.currentIndex = index;
  _audio.src = track.audioUrl;
  _audio.load();
  if (autoplay) {
    void _audio.play();
    _state.playing = true;
  } else {
    _state.playing = false;
  }

  localStorage.setItem(LS_LAST_TRACK, track.id);
  _renderNowPlaying();
  _renderTrackList();
}

function _togglePlay(): void {
  if (!_audio) return;
  if (_state.tracks.length === 0) return;

  // If no src loaded yet, load current track.
  if (!_audio.src || _audio.src === window.location.href) {
    _loadTrack(_state.currentIndex, true);
    return;
  }

  if (_audio.paused) {
    void _audio.play();
    _state.playing = true;
  } else {
    _audio.pause();
    _state.playing = false;
  }
}

function _skipTrack(direction: 'next' | 'prev'): void {
  if (_state.tracks.length === 0) return;
  const newIdx = nextIndex(
    _state.currentIndex,
    _state.tracks.length,
    direction,
    _state.shuffle,
  );
  _loadTrack(newIdx, _state.playing || direction === 'next');
}

function _toggleShuffle(): void {
  _state.shuffle = !_state.shuffle;
  const btn = $('suno-btn-shuffle');
  if (btn) {
    btn.classList.toggle('active', _state.shuffle);
    btn.setAttribute('aria-pressed', String(_state.shuffle));
  }
}

// ─── UI update helpers ────────────────────────────────────────────────────────

function _setPlayingUI(playing: boolean): void {
  _state.playing = playing;
  const btn = $('suno-btn-play');
  if (btn) btn.textContent = playing ? '⏸' : '▶';
  _syncViz();
}

function _onTimeUpdate(): void {
  if (!_audio || !isFinite(_audio.duration)) return;
  const pct = (_audio.currentTime / _audio.duration) * 1000;

  const scrub = $<HTMLInputElement>('suno-scrub');
  if (scrub) scrub.value = String(pct);

  const elapsed = $('suno-elapsed');
  if (elapsed) elapsed.textContent = fmtTrackDuration(_audio.currentTime);

  const remain = $('suno-remaining');
  if (remain) {
    const left = _audio.duration - _audio.currentTime;
    remain.textContent = `-${fmtTrackDuration(left)}`;
  }
}

function _renderNowPlaying(): void {
  const track = _state.tracks[_state.currentIndex];

  const artEl    = $<HTMLImageElement>('suno-art');
  const titleEl  = $('suno-now-title');
  const artistEl = $('suno-now-artist');
  const durEl    = $('suno-duration');
  const miniEl   = $('suno-mini-title'); // top-bar mini player

  if (!track) {
    if (artEl)    { artEl.src = ''; artEl.alt = ''; }
    if (titleEl)  titleEl.textContent = 'No tracks';
    if (artistEl) artistEl.textContent = '';
    if (durEl)    durEl.textContent = '--:--';
    if (miniEl)   { miniEl.textContent = 'No track'; miniEl.title = ''; }
    return;
  }

  if (miniEl) {
    miniEl.textContent = track.title;
    miniEl.title = `${track.title} — ${track.artist}`;
  }

  if (artEl) {
    artEl.src = track.imageUrl ?? '';
    artEl.alt = escHtml(track.title);
  }
  if (titleEl)  titleEl.textContent = track.title;
  if (artistEl) artistEl.textContent = track.artist;
  if (durEl)    durEl.textContent = track.durationFmt;

  // Reset scrub position when changing track.
  const scrub = $<HTMLInputElement>('suno-scrub');
  if (scrub) scrub.value = '0';

  const elapsed = $('suno-elapsed');
  if (elapsed) elapsed.textContent = '0:00';

  const remain = $('suno-remaining');
  if (remain) remain.textContent = `-${track.durationFmt}`;
}

function _renderTrackList(): void {
  const list = $('suno-track-list');
  if (!list) return;

  const total = _state.tracks.length;

  // Update the count chip ("shown / total" when filtering, else just total).
  const countEl = $('suno-count');

  if (total === 0) {
    list.innerHTML = '<p class="offline-state">No tracks in library.</p>';
    if (countEl) countEl.textContent = '0';
    return;
  }

  const visible = filterTracks(_state.tracks, _state.filter);

  if (countEl) {
    countEl.textContent =
      visible.length === total ? String(total) : `${visible.length}/${total}`;
  }

  if (visible.length === 0) {
    list.innerHTML = '<p class="offline-state">No tracks match.</p>';
    return;
  }

  // data-idx holds the ABSOLUTE index in _state.tracks so playback still
  // addresses the right track after filtering.
  list.innerHTML = visible.map(([i, t]) => `
    <div
      class="suno-track-row${i === _state.currentIndex ? ' suno-track-active' : ''}"
      data-idx="${i}"
      role="button"
      tabindex="0"
      aria-label="Play ${escHtml(t.title)}"
    >
      <span class="suno-track-play-indicator">${i === _state.currentIndex ? '♦' : '·'}</span>
      <div class="suno-track-info">
        <span class="suno-track-title">${escHtml(t.title)}</span>
        <span class="suno-track-artist">${escHtml(t.artist)}</span>
      </div>
      <span class="suno-track-dur">${escHtml(t.durationFmt)}</span>
    </div>
  `).join('');

  // One delegated listener for the whole list (547 rows → 1 handler, and it
  // survives re-renders without stacking). Bound once per list element.
  if (list.dataset['delegated'] !== '1') {
    const playFromEvent = (e: Event): void => {
      const row = (e.target as HTMLElement | null)?.closest<HTMLElement>(
        '.suno-track-row',
      );
      if (!row) return;
      const idx = parseInt(row.dataset['idx'] ?? '0', 10);
      _loadTrack(idx, true);
    };
    list.addEventListener('click', playFromEvent);
    list.addEventListener('keydown', (e) => {
      const ke = e as KeyboardEvent;
      if (ke.key === 'Enter' || ke.key === ' ') {
        e.preventDefault();
        playFromEvent(e);
      }
    });
    list.dataset['delegated'] = '1';
  }
}
