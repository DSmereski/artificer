/**
 * panels/music.ts — Local-folder music source for the Hive player.
 *
 * F4.2 — source switcher + mouse-only folder browser + track loading.
 * F4.3 — full transport (seek/volume/shuffle/repeat), queue, search, persistence.
 * F4.4 — fx-glass/fx-glow applied in HTML; viz reacts to the shared <audio>.
 *
 * Architecture: this module owns the "local" source state. The shared <audio>
 * element lives in panels/suno.ts (initSunoPlayer wires it). When local mode is
 * active, this module sets the audio.src via musicStreamUrl and delegates
 * play/pause/seek/volume to the same element obtained from getSunoAudio().
 *
 * Pure logic (shape, filter, nextIndex, repeat) is exported for unit tests.
 *
 * Mouse-only path: the folder browser shows clickable directory entries fetched
 * from /v1/music/browse. "Use this folder" calls /v1/music/tracks to load the
 * queue. No keyboard input is required to browse or play.
 */

import { escHtml } from '../format.js';
import {
  musicBrowse,
  getMusicFolders,
  addMusicFolder,
  getMusicTracks,
  musicStreamUrl,
  musicArtUrl,
  type MusicTrack,
  type MusicBrowseEntry,
  type MusicFolder,
} from '../gateway.js';
import { fmtTrackDuration } from './suno.js';

// ─── Storage keys ─────────────────────────────────────────────────────────────

const LS_SOURCE    = 'player:source';          // 'suno' | 'local'
const LS_FOLDER    = 'music:lastFolderId';
const LS_SHUFFLE   = 'music:shuffle';
const LS_REPEAT    = 'music:repeat';           // 'off' | 'all' | 'one'
const LS_TRACK_IDX = 'music:trackIdx';

// ─── Types ────────────────────────────────────────────────────────────────────

export type RepeatMode = 'off' | 'all' | 'one';

export interface LocalPlayerTrack {
  id: string;
  title: string;
  artist: string;
  album: string;
  durationFmt: string;
  audioUrl: string;
  artUrl: string;
}

// ─── Pure helpers (exported for unit tests) ───────────────────────────────────

/**
 * Shape raw MusicTrack objects into the minimal player shape.
 */
export function shapeLocalTracks(raw: MusicTrack[]): LocalPlayerTrack[] {
  return raw.map((t) => ({
    id:          t.id,
    title:       t.title  || filenameFallback(t.id),
    artist:      t.artist || 'Unknown',
    album:       t.album  || '',
    durationFmt: fmtTrackDuration(t.duration_s),
    audioUrl:    musicStreamUrl(t.id),
    artUrl:      musicArtUrl(t.id),
  }));
}

/**
 * Derive a human-readable title from a track ID (path-like string).
 * e.g. "/music/Folder/Track 01 - Song.mp3" → "Track 01 - Song"
 */
export function filenameFallback(id: string): string {
  const part = id.split(/[/\\]/).pop() ?? id;
  return part.replace(/\.[^.]+$/, '').trim() || 'Unknown';
}

/**
 * Filter local tracks by query (case-insensitive, title OR artist).
 * Returns [absoluteIndex, track] pairs so the queue can address by real index.
 */
export function filterLocalTracks(
  tracks: LocalPlayerTrack[],
  query: string,
): Array<[number, LocalPlayerTrack]> {
  const q = query.trim().toLowerCase();
  const all: Array<[number, LocalPlayerTrack]> = tracks.map((t, i) => [i, t]);
  if (!q) return all;
  return all.filter(
    ([, t]) =>
      t.title.toLowerCase().includes(q) ||
      t.artist.toLowerCase().includes(q) ||
      t.album.toLowerCase().includes(q),
  );
}

/**
 * Advance the track index given current, list length, direction, and repeat mode.
 * On repeat-one: always return current.
 * On repeat-all: wrap.
 * On off: clamp (next from last stays at last — caller should stop).
 */
export function nextLocalIndex(
  current: number,
  length: number,
  direction: 'next' | 'prev',
  shuffle: boolean,
  repeat: RepeatMode,
): number {
  if (length === 0) return 0;
  if (repeat === 'one') return current;
  if (shuffle) {
    if (length === 1) return 0;
    let idx = Math.floor(Math.random() * (length - 1));
    if (idx >= current) idx += 1;
    return idx;
  }
  if (direction === 'next') {
    if (repeat === 'all') return (current + 1) % length;
    return Math.min(current + 1, length - 1);
  }
  // prev
  if (repeat === 'all') return (current - 1 + length) % length;
  return Math.max(current - 1, 0);
}

/**
 * Determine whether playback should stop at end-of-queue.
 * True when repeat=off AND we are at the last track going next (without shuffle).
 */
export function shouldStopAtEnd(
  current: number,
  length: number,
  shuffle: boolean,
  repeat: RepeatMode,
): boolean {
  if (length === 0) return false;
  if (repeat !== 'off' || shuffle) return false;
  return current >= length - 1;
}

// ─── Module state ─────────────────────────────────────────────────────────────

interface MusicState {
  tracks: LocalPlayerTrack[];
  currentIndex: number;
  playing: boolean;
  shuffle: boolean;
  repeat: RepeatMode;
  filter: string;
  /** Current browse path (null = showing roots / saved folders). */
  browsePath: string | null;
  savedFolders: MusicFolder[];
}

const _s: MusicState = {
  tracks:       [],
  currentIndex: 0,
  playing:      false,
  shuffle:      false,
  repeat:       'off',
  filter:       '',
  browsePath:   null,
  savedFolders: [],
};

let _audio: HTMLAudioElement | null = null;

// ─── DOM helpers ──────────────────────────────────────────────────────────────

function $<T extends HTMLElement>(id: string): T | null {
  return document.getElementById(id) as T | null;
}

// ─── Public: receive the shared audio element from suno.ts ───────────────────

export function setMusicAudio(audio: HTMLAudioElement): void {
  _audio = audio;
}

// ─── Source mode ──────────────────────────────────────────────────────────────

export type PlayerSource = 'suno' | 'local';

let _activeSource: PlayerSource = 'suno';

export function getActiveSource(): PlayerSource {
  return _activeSource;
}

export function setActiveSource(src: PlayerSource): void {
  _activeSource = src;
  localStorage.setItem(LS_SOURCE, src);
  _renderSourceSwitcher();
  _renderBrowserOrQueue();
  // Pause playback when switching sources so we don't fight over <audio>.
  if (_audio && !_audio.paused) _audio.pause();
}

// ─── Init ─────────────────────────────────────────────────────────────────────

export function initMusicPlayer(): void {
  // Restore persisted preferences.
  const savedSrc = localStorage.getItem(LS_SOURCE);
  if (savedSrc === 'local') _activeSource = 'local';

  const savedShuffle = localStorage.getItem(LS_SHUFFLE);
  _s.shuffle = savedShuffle === 'true';

  const savedRepeat = localStorage.getItem(LS_REPEAT);
  if (savedRepeat === 'all' || savedRepeat === 'one' || savedRepeat === 'off') {
    _s.repeat = savedRepeat;
  }

  // Wire source switcher buttons.
  $('music-src-suno')?.addEventListener('click', () => setActiveSource('suno'));
  $('music-src-local')?.addEventListener('click', () => setActiveSource('local'));

  // Wire local transport buttons (re-use existing suno btns for play/prev/next;
  // these are already wired by suno.ts but we intercept when source===local).
  $('music-btn-shuffle')?.addEventListener('click', () => _toggleShuffle());
  $('music-btn-repeat')?.addEventListener('click', () => _cycleRepeat());

  // Wire seek (the shared #suno-scrub is already wired by suno.ts for Suno;
  // for local we need the same behaviour — audio.currentTime is set the same way).
  // suno.ts already handles this via timeupdate → _onTimeUpdate, which we hook
  // by sharing the same <audio> element. No re-wiring needed.

  // Wire local search box.
  const search = $<HTMLInputElement>('music-search');
  if (search) {
    search.addEventListener('input', () => {
      _s.filter = search.value;
      _renderQueue();
    });
  }

  // Wire audio events for local source.
  if (_audio) {
    _audio.addEventListener('ended', () => {
      if (_activeSource !== 'local') return;
      if (shouldStopAtEnd(_s.currentIndex, _s.tracks.length, _s.shuffle, _s.repeat)) {
        _s.playing = false;
        return;
      }
      const next = nextLocalIndex(_s.currentIndex, _s.tracks.length, 'next', _s.shuffle, _s.repeat);
      _loadLocalTrack(next, true);
    });

    _audio.addEventListener('play',  () => { if (_activeSource === 'local') _s.playing = true; });
    _audio.addEventListener('pause', () => { if (_activeSource === 'local') _s.playing = false; });
  }

  _renderSourceSwitcher();

  // If restoring local source, try to reload the last folder.
  if (_activeSource === 'local') {
    const savedFolder = localStorage.getItem(LS_FOLDER);
    if (savedFolder) {
      void _loadFolder(savedFolder);
    } else {
      void _startBrowse(null);
    }
  }
}

// ─── Folder browser ───────────────────────────────────────────────────────────

async function _startBrowse(path: string | null): Promise<void> {
  _s.browsePath = path;
  await _renderBrowserOrQueue();
}

async function _renderBrowserOrQueue(): Promise<void> {
  const container = $('music-browser-area');
  if (!container) return;

  if (_activeSource !== 'local') {
    container.innerHTML = '';
    return;
  }

  if (_s.tracks.length > 0) {
    // Already have a queue loaded — show it.
    _renderQueue();
    return;
  }

  // Show the folder browser.
  container.innerHTML = '<div class="music-browser-loading">Loading…</div>';

  let entries: MusicBrowseEntry[] = [];
  let savedFolders: MusicFolder[] = [];

  if (_s.browsePath === null) {
    // Root view: show saved folders first, then filesystem roots.
    [entries, savedFolders] = await Promise.all([
      musicBrowse(),
      getMusicFolders(),
    ]);
    _s.savedFolders = savedFolders;
  } else {
    entries = await musicBrowse(_s.browsePath);
  }

  _renderBrowser(entries, savedFolders);
}

function _renderBrowser(entries: MusicBrowseEntry[], savedFolders: MusicFolder[]): void {
  const container = $('music-browser-area');
  if (!container) return;

  let html = '';

  // "Back" button when not at root.
  if (_s.browsePath !== null) {
    html += `<button class="music-browser-back" id="music-browse-back" aria-label="Go up">&#x2190; back</button>`;
  }

  // Saved folders section (root view only).
  if (_s.browsePath === null && savedFolders.length > 0) {
    html += '<div class="music-browser-section-label">Saved folders</div>';
    for (const f of savedFolders) {
      html += `
        <div class="music-browser-entry music-browser-saved" data-path="${escHtml(f.id)}" data-is-saved="1" role="button" tabindex="0">
          <span class="music-browser-icon">&#x1F4C2;</span>
          <span class="music-browser-name">${escHtml(f.name)}</span>
          <button class="music-browser-use" data-folder-id="${escHtml(f.id)}" aria-label="Play this folder">&#x25B6; use</button>
        </div>`;
    }
    if (entries.length > 0) {
      html += '<div class="music-browser-section-label">Browse filesystem</div>';
    }
  }

  // Filesystem entries. The gateway's /browse returns directories only (it lists
  // subdirectories), and does not send an is_dir flag — so only skip an entry
  // that is EXPLICITLY a non-directory; treat missing/true as a folder.
  for (const e of entries) {
    if (e.is_dir === false) continue;
    html += `
      <div class="music-browser-entry" data-path="${escHtml(e.path)}" role="button" tabindex="0">
        <span class="music-browser-icon">&#x1F4C1;</span>
        <span class="music-browser-name">${escHtml(e.name)}</span>
        <button class="music-browser-use" data-folder-path="${escHtml(e.path)}" aria-label="Play this folder">&#x25B6; use</button>
      </div>`;
  }

  if (html === '' || (entries.length === 0 && savedFolders.length === 0)) {
    html = '<div class="music-browser-empty">No folders found.<br>The gateway may not have allowed roots configured.</div>';
  }

  container.innerHTML = html;

  // Bind click handlers (delegated).
  container.addEventListener('click', _onBrowserClick, { once: false });

  const back = $('music-browse-back');
  back?.addEventListener('click', (e) => {
    e.stopPropagation();
    void _startBrowse(null);
  });
}

function _onBrowserClick(e: Event): void {
  const target = e.target as HTMLElement;

  // "use" button — load tracks from this folder.
  const useBtn = target.closest<HTMLButtonElement>('.music-browser-use');
  if (useBtn) {
    e.stopPropagation();
    const folderId   = useBtn.dataset['folderId'];
    const folderPath = useBtn.dataset['folderPath'];
    if (folderId) {
      void _loadFolder(folderId);
    } else if (folderPath) {
      // Not yet saved — add it first, then load.
      void _addAndLoadFolder(folderPath);
    }
    return;
  }

  // Entry click — navigate into directory.
  const entry = target.closest<HTMLElement>('.music-browser-entry');
  if (entry) {
    const path = entry.dataset['path'];
    if (path) void _startBrowse(path);
  }
}

async function _addAndLoadFolder(path: string): Promise<void> {
  const folder = await addMusicFolder(path);
  if (folder) {
    localStorage.setItem(LS_FOLDER, folder.id);
    await _loadFolder(folder.id);
  } else {
    // Fallback: load by path directly.
    await _loadFolderByPath(path);
  }
}

async function _loadFolder(folderId: string): Promise<void> {
  localStorage.setItem(LS_FOLDER, folderId);
  const raw = await getMusicTracks(folderId);
  _initQueue(raw);
}

async function _loadFolderByPath(path: string): Promise<void> {
  const raw = await getMusicTracks(path);
  _initQueue(raw);
}

function _initQueue(raw: MusicTrack[]): void {
  _s.tracks = shapeLocalTracks(raw);
  _s.filter = '';

  // Restore last position.
  const savedIdx = parseInt(localStorage.getItem(LS_TRACK_IDX) ?? '0', 10);
  _s.currentIndex = isFinite(savedIdx) && savedIdx < _s.tracks.length ? savedIdx : 0;

  _renderNowPlaying();
  _renderQueue();
}

// ─── Playback ─────────────────────────────────────────────────────────────────

function _loadLocalTrack(index: number, autoplay = false): void {
  if (!_audio || _s.tracks.length === 0) return;
  const track = _s.tracks[index];
  if (!track) return;

  _s.currentIndex = index;
  localStorage.setItem(LS_TRACK_IDX, String(index));

  // Set audio source — the gateway Range stream supports seeking.
  _audio.src = track.audioUrl;
  _audio.load();

  if (autoplay) {
    void _audio.play();
    _s.playing = true;
  } else {
    _s.playing = false;
  }

  _renderNowPlaying();
  _renderQueue();
}

/** Called by suno.ts when the play button is clicked in local mode. */
export function localTogglePlay(): void {
  if (!_audio) return;
  if (_s.tracks.length === 0) {
    // No tracks yet — open the browser.
    void _startBrowse(null);
    return;
  }
  if (!_audio.src || _audio.src === window.location.href) {
    _loadLocalTrack(_s.currentIndex, true);
    return;
  }
  if (_audio.paused) {
    void _audio.play();
    _s.playing = true;
  } else {
    _audio.pause();
    _s.playing = false;
  }
}

/** Called by suno.ts prev/next buttons in local mode. */
export function localSkip(direction: 'next' | 'prev'): void {
  if (_s.tracks.length === 0) return;
  const next = nextLocalIndex(_s.currentIndex, _s.tracks.length, direction, _s.shuffle, _s.repeat);
  _loadLocalTrack(next, _s.playing || direction === 'next');
}

function _toggleShuffle(): void {
  _s.shuffle = !_s.shuffle;
  localStorage.setItem(LS_SHUFFLE, String(_s.shuffle));
  _renderTransportState();
}

function _cycleRepeat(): void {
  const cycle: RepeatMode[] = ['off', 'all', 'one'];
  const idx = cycle.indexOf(_s.repeat);
  _s.repeat = cycle[(idx + 1) % cycle.length] ?? 'off';
  localStorage.setItem(LS_REPEAT, _s.repeat);
  _renderTransportState();
}

// ─── Render helpers ───────────────────────────────────────────────────────────

function _renderSourceSwitcher(): void {
  const sunoBtn  = $('music-src-suno');
  const localBtn = $('music-src-local');
  if (sunoBtn)  sunoBtn.classList.toggle('active', _activeSource === 'suno');
  if (localBtn) localBtn.classList.toggle('active', _activeSource === 'local');

  // Show/hide the local controls section.
  const localSection = $('music-local-section');
  if (localSection) localSection.hidden = _activeSource !== 'local';

  // Show/hide the suno track list.
  const sunoSection = $('suno-suno-section');
  if (sunoSection) sunoSection.hidden = _activeSource !== 'suno';
}

function _renderNowPlaying(): void {
  if (_activeSource !== 'local') return;
  const track = _s.tracks[_s.currentIndex];

  const artEl    = $<HTMLImageElement>('suno-art');
  const titleEl  = $('suno-now-title');
  const artistEl = $('suno-now-artist');
  const durEl    = $('suno-duration');
  const miniEl   = $('suno-mini-title');

  if (!track) {
    if (titleEl)  titleEl.textContent = 'No tracks';
    if (artistEl) artistEl.textContent = '';
    if (durEl)    durEl.textContent = '--:--';
    if (miniEl)   { miniEl.textContent = 'No track'; miniEl.title = ''; }
    return;
  }

  if (artEl) {
    artEl.src = track.artUrl;
    artEl.alt = escHtml(track.title);
    // On art-load error, replace with generated tile.
    artEl.onerror = () => { artEl.src = _generateArtTile(track.title); artEl.onerror = null; };
  }
  if (titleEl)  titleEl.textContent = track.title;
  if (artistEl) artistEl.textContent = track.artist || track.album || '';
  if (durEl)    durEl.textContent = track.durationFmt;
  if (miniEl) {
    miniEl.textContent = track.title;
    miniEl.title = `${track.title} — ${track.artist}`;
  }

  const scrub = $<HTMLInputElement>('suno-scrub');
  if (scrub) scrub.value = '0';
  const elapsed  = $('suno-elapsed');
  if (elapsed) elapsed.textContent = '0:00';
  const remain   = $('suno-remaining');
  if (remain) remain.textContent = `-${track.durationFmt}`;
}

function _renderQueue(): void {
  const container = $('music-browser-area');
  if (!container) return;
  if (_activeSource !== 'local') return;
  if (_s.tracks.length === 0) return; // stay in browser view

  const visible = filterLocalTracks(_s.tracks, _s.filter);
  const count   = $('music-count');
  if (count) {
    count.textContent = visible.length === _s.tracks.length
      ? String(_s.tracks.length)
      : `${visible.length}/${_s.tracks.length}`;
  }

  if (visible.length === 0) {
    container.innerHTML = '<p class="offline-state">No tracks match.</p>';
    return;
  }

  const activeClass = (i: number): string =>
    i === _s.currentIndex ? ' music-track-active fx-glow' : '';

  container.innerHTML = visible.map(([i, t]) => `
    <div
      class="music-track-row${activeClass(i)}"
      data-idx="${i}"
      role="button"
      tabindex="0"
      aria-label="Play ${escHtml(t.title)}"
    >
      <span class="music-track-indicator">${i === _s.currentIndex ? '&#x2666;' : '&middot;'}</span>
      <div class="music-track-info">
        <span class="music-track-title">${escHtml(t.title)}</span>
        <span class="music-track-artist">${escHtml(t.artist)}</span>
      </div>
      <span class="music-track-dur">${escHtml(t.durationFmt)}</span>
    </div>
  `).join('');

  // Delegated click handler.
  if (container.dataset['musicDelegated'] !== '1') {
    container.addEventListener('click', (e) => {
      const row = (e.target as HTMLElement).closest<HTMLElement>('.music-track-row');
      if (!row) return;
      const idx = parseInt(row.dataset['idx'] ?? '0', 10);
      _loadLocalTrack(idx, true);
    });
    container.dataset['musicDelegated'] = '1';
  }

  // Also wire a "change folder" button if we already have tracks.
  _renderChangeFolderBtn();
}

function _renderChangeFolderBtn(): void {
  let btn = $('music-change-folder');
  if (!btn) {
    btn = document.createElement('button');
    btn.id = 'music-change-folder';
    btn.className = 'suno-btn music-change-folder-btn';
    btn.textContent = '&#x1F4C2; change folder';
    btn.addEventListener('click', () => {
      _s.tracks = [];
      _s.filter = '';
      void _startBrowse(null);
    });
    $('music-local-section')?.prepend(btn);
  }
}

function _renderTransportState(): void {
  const shuffleBtn = $('music-btn-shuffle');
  if (shuffleBtn) {
    shuffleBtn.classList.toggle('active', _s.shuffle);
    shuffleBtn.setAttribute('aria-pressed', String(_s.shuffle));
  }

  const repeatBtn = $('music-btn-repeat');
  if (repeatBtn) {
    const label = { off: '&#x27F3; off', all: '&#x27F3; all', one: '&#x27F3; one' };
    repeatBtn.innerHTML = label[_s.repeat];
    repeatBtn.classList.toggle('active', _s.repeat !== 'off');
    repeatBtn.setAttribute('aria-label', `Repeat ${_s.repeat}`);
  }
}

// ─── Generated art tile fallback ──────────────────────────────────────────────

function _generateArtTile(title: string): string {
  const canvas = document.createElement('canvas');
  canvas.width = 48; canvas.height = 48;
  const ctx = canvas.getContext('2d');
  if (!ctx) return '';

  // Hash-based hue so same title always gets same colour.
  let hash = 0;
  for (let i = 0; i < title.length; i++) hash = (hash * 31 + title.charCodeAt(i)) >>> 0;
  const hue = hash % 360;

  ctx.fillStyle = `oklch(0.25 0.04 ${hue})`;
  ctx.fillRect(0, 0, 48, 48);

  // Initial letter.
  ctx.fillStyle = `oklch(0.75 0.12 ${hue})`;
  ctx.font = 'bold 24px sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText((title[0] ?? '?').toUpperCase(), 24, 24);

  return canvas.toDataURL();
}
