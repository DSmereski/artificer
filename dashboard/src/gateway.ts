/**
 * gateway.ts — typed HTTP client for the Hive gateway.
 *
 * Ported from C:\Projects\***REMOVED***\src\gateway.ts and extended for:
 *  - Bearer auth for /v1/* endpoints
 *  - All Phase A endpoints: /board/stats + /board/state
 *  - Offline / error resilience (fetchWithTimeout, AbortController)
 *
 * CORS note: Lively WebView2 loads as file:// origin, no CORS enforcement.
 * In Vite dev, CORS applies → /api proxy in vite.config.ts routes to gateway.
 */

// In Vite dev mode (npm run dev): use /api proxy to avoid CORS.
// In production (dist/ loaded by Lively as file://): hit gateway directly.
const BASE_URL: string = import.meta.env.DEV
  ? '/api'
  : 'http://127.0.0.1:8766';

// The gateway's real loopback origin. Used for contexts that must hit the
// gateway directly (the embedded /board iframe), NOT via the dev /api proxy:
// the iframe is its OWN browsing context that loads a gateway-served HTML page
// and issues same-origin fetches (e.g. absolute `/board/state`). Routing it
// through the proxy would make those absolute fetches resolve against the vite
// origin and return index.html. Same-origin iframe internals have no CORS.
const GATEWAY_ORIGIN = 'http://127.0.0.1:8766';

// #182/#183: tell the gateway the active theme so it can (a) serve it to the
// phone app for cross-device sync, and (b) set the Windows OS accent to match the
// wallpaper. Loopback PUT; failures are non-fatal.
if (typeof window !== 'undefined') {
  const pushTheme = (): void => {
    const theme = document.documentElement.dataset.theme || 'hive-v2';
    fetch(`${GATEWAY_ORIGIN}/v1/theme`, {
      method: 'PUT',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ theme }),
    }).catch(() => {});
  };
  // On every theme switch (the ◑ button dispatches this).
  window.addEventListener('hive-theme-change', pushTheme);
  // AND once on load, so the OS accent follows the persisted theme without a
  // manual cycle — the missing sync that left the accent stuck after a reboot,
  // a reload, or a stale wallpaper build that never re-PUT the theme.
  pushTheme();
}

const TIMEOUT_MS = 8_000;
// Evolve (Suggest/Go) runs the repo analyzer + a planner-qwen synthesis on the
// gateway — that's an LLM call that can take minutes, far longer than the 8s
// board-read timeout. Without this, the fetch aborts ("signal is aborted
// without reason") before the analysis returns.
const EVOLVE_TIMEOUT_MS = 180_000;

// ─── Runtime bearer token (set via Lively Properties / localStorage) ─────────

let _bearerToken: string | null = null;

export function setBearerToken(token: string | null): void {
  _bearerToken = token;
}

export function getBearerToken(): string | null {
  return _bearerToken;
}

// ─── /board/list types (P2 v-Next) ──────────────────────────────────────────

export interface BoardInfo {
  board_id: string;
  name: string;
  description: string;
  created_at: string;
}

// ─── /board/stats types (matches gateway contract) ───────────────────────────

export interface SmokeStats {
  pass: number;
  fail: number;
}

export interface TokenStats {
  hive: number;
  claude: number;
}

export interface BoardStats {
  by_status: Record<string, number>;
  by_assignee: Record<string, number>;
  tokens: TokenStats;
  avg_tokens_per_task?: Record<string, number>;
  avg_attempts?: number;
  smoke: SmokeStats;
  cost_usd: number;
  lessons?: number;
  parse_fail?: { rate: number; fails: number; turns: number };
  top_projects?: Array<{ slug: string; done: number; active: number }>;
}

// ─── /board/state types ───────────────────────────────────────────────────────

export interface ContentSpec {
  type?: string;            // image | video
  prompt?: string;
  count?: number;
  state?: string;           // queued | done | error
  result_media_ids?: string[];
  error?: string;
}

export interface BoardTask {
  slug: string;
  title: string;
  status: string;
  project_slug?: string;
  assignee?: string;
  last_action?: string;
  agent_turns?: number;
  hive_tokens?: number;
  claude_tokens?: number;
  smoke_ok?: boolean;
  updated_at?: string;
  kind?: string;            // 'code' | 'content'
  content_spec?: ContentSpec;
}

export interface BoardProject {
  slug: string;
  status?: string;
}

export interface BoardState {
  tasks: BoardTask[];
  projects: BoardProject[];
  pending_approvals?: unknown[];
  paused?: boolean;
}

// ─── Fetch helpers ────────────────────────────────────────────────────────────

async function fetchWithTimeout(
  url: string,
  options: RequestInit = {},
  timeoutMs: number = TIMEOUT_MS,
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

function authHeaders(): HeadersInit {
  if (_bearerToken) {
    return { Authorization: `Bearer ${_bearerToken}` };
  }
  return {};
}

// ─── Public API ───────────────────────────────────────────────────────────────

export async function getBoardStats(board?: string): Promise<BoardStats> {
  const url = board
    ? `${BASE_URL}/board/stats?board=${encodeURIComponent(board)}`
    : `${BASE_URL}/board/stats`;
  const res = await fetchWithTimeout(url);
  if (!res.ok) {
    throw new Error(`/board/stats returned HTTP ${res.status}`);
  }
  return (await res.json()) as BoardStats;
}

export async function getBoardState(board?: string): Promise<BoardState> {
  const url = board
    ? `${BASE_URL}/board/state?board=${encodeURIComponent(board)}`
    : `${BASE_URL}/board/state`;
  const res = await fetchWithTimeout(url);
  if (!res.ok) {
    throw new Error(`/board/state returned HTTP ${res.status}`);
  }
  return (await res.json()) as BoardState;
}

/**
 * GET /board/list — returns the registered boards. Returns [] on error.
 */
export async function getBoardList(): Promise<BoardInfo[]> {
  try {
    const res = await fetchWithTimeout(`${BASE_URL}/board/list`);
    if (!res.ok) return [];
    return (await res.json()) as BoardInfo[];
  } catch {
    return [];
  }
}

// ─── /board/tokens-by-day types ──────────────────────────────────────────────

export interface TokensByDayEntry {
  date: string;    // 'YYYY-MM-DD'
  hive: number;
  claude: number;
  total: number;
}

/**
 * GET /board/tokens-by-day?days=<n>
 *
 * Open endpoint — no Bearer token required.  Returns an array of per-day
 * token counts (ascending by date, zero-filled), length == days.
 * Returns an empty array on any error so callers can show a graceful
 * empty/loading state until the gateway is available.
 */
export async function tokensByDay(days = 30): Promise<TokensByDayEntry[]> {
  try {
    const res = await fetchWithTimeout(
      `${BASE_URL}/board/tokens-by-day?days=${days}`,
    );
    if (!res.ok) return [];
    return (await res.json()) as TokensByDayEntry[];
  } catch {
    return [];
  }
}

/**
 * Check that the gateway is reachable.
 * Returns true if /board/stats responds with HTTP 2xx.
 */
export async function pingGateway(): Promise<boolean> {
  try {
    const res = await fetchWithTimeout(`${BASE_URL}/board/stats`);
    return res.ok;
  } catch {
    return false;
  }
}

/**
 * Fetch a Bearer-protected /v1/* route.
 * Returns null (with a console warning) if no token is set.
 */
export async function fetchV1<T>(path: string): Promise<T | null> {
  // These read endpoints are loopback-exempt on the gateway (scout/status,
  // scout/history, escalations, calendar, graph/*), so the wallpaper dashboard
  // (which runs on 127.0.0.1) does NOT need a device token — just call. If a
  // Bearer IS set (e.g. a paired remote client), it's sent for non-loopback
  // access. A 401 (non-loopback, no token) surfaces as a throw the caller
  // already handles by degrading gracefully.
  const res = await fetchWithTimeout(`${BASE_URL}/v1${path}`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    throw new Error(`/v1${path} returned HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

// ─── Scout endpoints ──────────────────────────────────────────────────────────

import type { ScoutStatus, ScoutHistorySample, DockerStatus, GitActivity } from './types.js';

/** GET /v1/scout/status — live GPU, disk, bot, host info. Requires Bearer. */
export async function getScoutStatus(): Promise<ScoutStatus | null> {
  return fetchV1<ScoutStatus>('/scout/status');
}

// ─── GPU mode switch ("free the 4080") ────────────────────────────────────────

export interface GpuMode {
  mode: 'auto' | 'force_on' | 'force_off';
  gaming: boolean;
  ai_may_use_4080: boolean;
  ai_devices: string;
}

/** GET /v1/gpu-mode — current 4080 policy (loopback-exempt). */
export async function getGpuMode(): Promise<GpuMode | null> {
  try {
    return await fetchV1<GpuMode>('/gpu-mode');
  } catch {
    return null;
  }
}

/** PUT /v1/gpu-mode — set the 4080 policy. Returns the new status or null. */
export async function setGpuMode(mode: GpuMode['mode']): Promise<GpuMode | null> {
  try {
    const res = await fetchWithTimeout(`${BASE_URL}/v1/gpu-mode`, {
      method: 'PUT',
      headers: { ...authHeaders(), 'content-type': 'application/json' },
      body: JSON.stringify({ mode }),
    });
    if (!res.ok) return null;
    return (await res.json()) as GpuMode;
  } catch {
    return null;
  }
}

/** GET /v1/docker/status — local Docker containers (loopback-exempt). */
export async function getDockerStatus(): Promise<DockerStatus | null> {
  return fetchV1<DockerStatus>('/docker/status');
}

/** GET /v1/git/activity — recent commits across crew projects (loopback-exempt). */
export async function getGitActivity(): Promise<GitActivity | null> {
  return fetchV1<GitActivity>('/git/activity');
}

/** GET /v1/scout/history?since=<epoch_s>&limit=<n> — rolling samples. */
export async function getScoutHistory(
  since?: number,
  limit = 60,
): Promise<ScoutHistorySample[] | null> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (since != null) params.set('since', String(since));
  return fetchV1<ScoutHistorySample[]>(`/scout/history?${params.toString()}`);
}

// ─── Escalation endpoints ─────────────────────────────────────────────────────

import type { EscalationList } from './types.js';

/** GET /v1/escalations — open escalation list. Requires Bearer. */
export async function getEscalations(): Promise<EscalationList | null> {
  return fetchV1<EscalationList>('/escalations');
}

// ─── Calendar endpoints ───────────────────────────────────────────────────────

import type { CalendarJob } from './types.js';

/** GET /v1/calendar/jobs/upcoming?n=<n> — next N scheduled jobs. Requires Bearer. */
export async function getUpcomingJobs(n = 8): Promise<CalendarJob[] | null> {
  return fetchV1<CalendarJob[]>(`/calendar/jobs/upcoming?n=${n}`);
}

// ─── Suno endpoints ───────────────────────────────────────────────────────────

export interface SunoTrack {
  id: string;
  title: string;
  artist_name: string;
  tags: string | null;
  duration: number | null;
  image_url: string | null;
  play_count: number;
}

/**
 * GET /v1/suno/tracks — open endpoint, no bearer required.
 * Returns an empty array if the gateway returns an error.
 */
export async function sunoTracks(): Promise<SunoTrack[]> {
  try {
    const res = await fetchWithTimeout(`${BASE_URL}/v1/suno/tracks`);
    if (!res.ok) return [];
    return (await res.json()) as SunoTrack[];
  } catch {
    return [];
  }
}

/** Build the streaming audio URL for a track ID. */
export function sunoAudioUrl(id: string): string {
  // Track ids can be "Title [shorthash]" (spaces + brackets), so they MUST be
  // URL-encoded or the <audio> src is malformed and never loads (the dead play
  // button). encodeURIComponent keeps the path segment valid; the gateway decodes it.
  return `${BASE_URL}/v1/suno/audio/${encodeURIComponent(id)}`;
}

/**
 * URL for the full crew board in embed mode (header-stripped, framable).
 * Prod: loopback gateway. Vite dev: /api proxy. The embed page carries its own
 * X-Board-Token, so the iframe mutates without dashboard-side auth wiring.
 */
export function boardEmbedUrl(project?: string | null): string {
  // Always the direct gateway origin (never the dev /api proxy) — see
  // GATEWAY_ORIGIN. The embed page's own absolute fetches must resolve to the
  // gateway, which only happens when the iframe's document origin IS the gateway.
  // `project` (the dashboard's active project filter) is forwarded as a query
  // param; the embed page seeds its FILTER_PROJ from it so the framed board
  // scopes to that project. Re-pointing src reloads + re-filters.
  const base = `${GATEWAY_ORIGIN}/board?embed=1`;
  return project ? `${base}&project=${encodeURIComponent(project)}` : base;
}

// ─── Board mutations (CC1 command surface) ────────────────────────────────────
//
// Mutations need X-Board-Token. The dashboard runs on loopback, so it fetches
// the per-process token once from /board/session-token (loopback-only) and
// caches it. A device Bearer (if set) is sent too as a fallback.

let _boardToken: string | null = null;

export async function getBoardSessionToken(): Promise<string | null> {
  if (_boardToken) return _boardToken;
  try {
    const res = await fetchWithTimeout(`${BASE_URL}/board/session-token`);
    if (!res.ok) return null;
    const data = (await res.json()) as { token?: string };
    _boardToken = data.token ?? null;
    return _boardToken;
  } catch {
    return null;
  }
}

/** POST a board mutation with X-Board-Token (+ Bearer fallback). */
async function boardMutate(
  path: string,
  body?: unknown,
  timeoutMs?: number,
): Promise<Response> {
  const send = async (): Promise<Response> => {
    const token = await getBoardSessionToken();
    const headers: Record<string, string> = { 'content-type': 'application/json' };
    if (token) headers['X-Board-Token'] = token;
    if (_bearerToken) headers['Authorization'] = `Bearer ${_bearerToken}`;
    return fetchWithTimeout(`${BASE_URL}${path}`, {
      method: 'POST',
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    }, timeoutMs);
  };
  let r = await send();
  // The gateway regenerates its board token on every restart, so a long-lived
  // wallpaper holds a stale cached token and every mutation 401/403s silently
  // (the "approve does nothing" bug). On an auth failure, drop the cache, fetch
  // a fresh token, and retry once.
  if (r.status === 401 || r.status === 403) {
    _boardToken = null;
    r = await send();
  }
  return r;
}

export async function pauseBoard(): Promise<boolean> {
  const r = await boardMutate('/board/pause');
  return r.ok;
}

export async function resumeBoard(): Promise<boolean> {
  const r = await boardMutate('/board/resume');
  return r.ok;
}

export async function createBoardTask(input: {
  title: string;
  project_slug: string;
  body?: string;
}): Promise<boolean> {
  const r = await boardMutate('/board/tasks', input);
  return r.ok;
}

export async function decomposeGoal(input: {
  goal: string;
  project_slug: string;
}): Promise<boolean> {
  const r = await boardMutate('/board/decompose', input);
  return r.ok;
}

// ─── Evolve (continuous-dev) ───────────────────────────────────────────────────

export interface EvolveCandidate {
  title: string;
  body?: string;
  rationale?: string;
  source?: string[];
  score?: number;
  checklist?: string[];
}

/** Analyze a done project → ranked next-work candidates (no build). */
export async function evolveSuggest(slug: string): Promise<EvolveCandidate[]> {
  const r = await boardMutate(`/board/projects/${encodeURIComponent(slug)}/evolve/suggest`, undefined, EVOLVE_TIMEOUT_MS);
  const d = (await r.json().catch(() => ({}))) as { candidates?: EvolveCandidate[]; detail?: string };
  if (!r.ok) throw new Error(d.detail || `evolve/suggest HTTP ${r.status}`);
  return d.candidates ?? [];
}

/** CP2: draft a Karpathy master plan for a goal → lands in the Proposed lane
 *  for your approval (instead of building immediately). */
export async function proposePlan(
  slug: string, goal: string,
): Promise<{ slug: string; steps: number }> {
  const r = await boardMutate('/board/plans/propose', { project_slug: slug, goal }, EVOLVE_TIMEOUT_MS);
  const d = (await r.json().catch(() => ({}))) as { slug?: string; steps?: number; detail?: string };
  if (!r.ok) throw new Error(d.detail || `propose-plan HTTP ${r.status}`);
  return { slug: d.slug ?? '', steps: d.steps ?? 0 };
}

/** Build the top next-work candidate → a goal + tickets the hive picks up. */
export async function evolveGo(slug: string): Promise<{ created: number; evolved_from: string; project_slug: string }> {
  const r = await boardMutate(`/board/projects/${encodeURIComponent(slug)}/evolve/go`, {}, EVOLVE_TIMEOUT_MS);
  const d = (await r.json().catch(() => ({}))) as { created?: number; evolved_from?: string; project_slug?: string; detail?: string };
  if (!r.ok) throw new Error(d.detail || `evolve/go HTTP ${r.status}`);
  return { created: d.created ?? 0, evolved_from: d.evolved_from ?? '', project_slug: d.project_slug ?? slug };
}

/** Enable/disable a project for the hive (whether the dispatcher works it). */
export async function setProjectEnabled(slug: string, on: boolean): Promise<boolean> {
  const verb = on ? 'enable' : 'disable';
  const r = await boardMutate(`/board/projects/${encodeURIComponent(slug)}/${verb}`);
  return r.ok;
}

/** POST /board/boards — create a new board (P2 v-Next). Returns true on success. */
export async function createBoard(input: {
  board_id: string;
  name: string;
  description?: string;
}): Promise<boolean> {
  const r = await boardMutate('/board/boards', {
    board_id: input.board_id,
    name: input.name,
    description: input.description ?? '',
  });
  return r.ok;
}

export async function moveBoardTask(slug: string, status: string): Promise<boolean> {
  const r = await boardMutate(`/board/tasks/${slug}/move`, { status });
  return r.ok;
}

/** Create a content-generation request (image|video) as a board task. */
export async function createContent(input: {
  type: 'image' | 'video';
  prompt: string;
  count?: number;
  seed_media_id?: string;
}): Promise<boolean> {
  const r = await boardMutate('/board/content', input);
  return r.ok;
}

/** URL for a generated media id (loopback-exempt). */
export function mediaUrl(id: string): string {
  return `${BASE_URL}/v1/media/${id}`;
}

// ─── Music endpoints (F4) ─────────────────────────────────────────────────────

export interface MusicFolder {
  id: string;
  path: string;
  name: string;
}

export interface MusicBrowseEntry {
  name: string;
  path: string;
  is_dir: boolean;
}

export interface MusicTrack {
  id: string;
  title: string;
  artist: string;
  album: string;
  duration_s: number | null;
  track_no: number | null;
  folder_id?: string;
}

/**
 * GET /v1/music/browse?path=<dir>
 * Lists directory entries for mouse-only folder navigation.
 * Returns [] on error.
 */
export async function musicBrowse(path?: string): Promise<MusicBrowseEntry[]> {
  try {
    const url = path
      ? `${BASE_URL}/v1/music/browse?path=${encodeURIComponent(path)}`
      : `${BASE_URL}/v1/music/browse`;
    const res = await fetchWithTimeout(url);
    if (!res.ok) return [];
    // Gateway returns { dirs: [...], current }, not a bare array.
    const data = await res.json();
    return (Array.isArray(data) ? data : (data?.dirs ?? [])) as MusicBrowseEntry[];
  } catch {
    return [];
  }
}

/**
 * GET /v1/music/folders — list remembered library roots.
 * Returns [] on error.
 */
export async function getMusicFolders(): Promise<MusicFolder[]> {
  try {
    const res = await fetchWithTimeout(`${BASE_URL}/v1/music/folders`);
    if (!res.ok) return [];
    // Gateway returns { folders: [...] }, not a bare array.
    const data = await res.json();
    return (Array.isArray(data) ? data : (data?.folders ?? [])) as MusicFolder[];
  } catch {
    return [];
  }
}

/**
 * POST /v1/music/folders — remember a chosen folder path.
 * Returns the created MusicFolder or null on error.
 */
export async function addMusicFolder(path: string): Promise<MusicFolder | null> {
  try {
    const res = await fetchWithTimeout(`${BASE_URL}/v1/music/folders`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    if (!res.ok) return null;
    return (await res.json()) as MusicFolder;
  } catch {
    return null;
  }
}

/**
 * GET /v1/music/tracks?folder=<id|path>
 * Returns the track list for a given folder id or path.
 * Returns [] on error.
 */
export async function getMusicTracks(folder: string): Promise<MusicTrack[]> {
  try {
    const res = await fetchWithTimeout(
      `${BASE_URL}/v1/music/tracks?folder=${encodeURIComponent(folder)}`,
    );
    if (!res.ok) return [];
    // Gateway returns { tracks: [...] }, not a bare array.
    const data = await res.json();
    return (Array.isArray(data) ? data : (data?.tracks ?? [])) as MusicTrack[];
  } catch {
    return [];
  }
}

/**
 * Build the HTTP streaming URL for a local music track.
 * The gateway serves with Range support for seek/scrub.
 */
export function musicStreamUrl(id: string): string {
  return `${BASE_URL}/v1/music/stream/${encodeURIComponent(id)}`;
}

/**
 * Build the album-art URL for a local music track.
 * The gateway returns embedded art or a generated fallback tile.
 */
export function musicArtUrl(id: string): string {
  return `${BASE_URL}/v1/music/art/${encodeURIComponent(id)}`;
}
