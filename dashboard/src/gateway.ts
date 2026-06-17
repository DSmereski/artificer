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

const TIMEOUT_MS = 8_000;

// ─── Runtime bearer token (set via Lively Properties / localStorage) ─────────

let _bearerToken: string | null = null;

export function setBearerToken(token: string | null): void {
  _bearerToken = token;
}

export function getBearerToken(): string | null {
  return _bearerToken;
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
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
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

export async function getBoardStats(): Promise<BoardStats> {
  const res = await fetchWithTimeout(`${BASE_URL}/board/stats`);
  if (!res.ok) {
    throw new Error(`/board/stats returned HTTP ${res.status}`);
  }
  return (await res.json()) as BoardStats;
}

export async function getBoardState(): Promise<BoardState> {
  const res = await fetchWithTimeout(`${BASE_URL}/board/state`);
  if (!res.ok) {
    throw new Error(`/board/state returned HTTP ${res.status}`);
  }
  return (await res.json()) as BoardState;
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
  return `${BASE_URL}/v1/suno/audio/${id}`;
}

/**
 * URL for the full crew board in embed mode (header-stripped, framable).
 * Prod: loopback gateway. Vite dev: /api proxy. The embed page carries its own
 * X-Board-Token, so the iframe mutates without dashboard-side auth wiring.
 */
export function boardEmbedUrl(): string {
  // Always the direct gateway origin (never the dev /api proxy) — see
  // GATEWAY_ORIGIN. The embed page's own absolute fetches must resolve to the
  // gateway, which only happens when the iframe's document origin IS the gateway.
  return `${GATEWAY_ORIGIN}/board?embed=1`;
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
async function boardMutate(path: string, body?: unknown): Promise<Response> {
  const token = await getBoardSessionToken();
  const headers: Record<string, string> = { 'content-type': 'application/json' };
  if (token) headers['X-Board-Token'] = token;
  if (_bearerToken) headers['Authorization'] = `Bearer ${_bearerToken}`;
  return fetchWithTimeout(`${BASE_URL}${path}`, {
    method: 'POST',
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
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
