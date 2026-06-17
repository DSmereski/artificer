/**
 * rendergate/mock.ts — intercepts every gateway request from the built
 * dashboard and fulfils it from the seeded fixtures. The dashboard's prod
 * build hits http://127.0.0.1:8766 directly, so a single route over that
 * origin covers all HTTP (board, /v1/*, suno) plus the embedded board iframe.
 *
 * WebSocket endpoints (event ticker, terminal PTY) are NOT mocked — they fail
 * to connect and the dashboard degrades gracefully (reconnect/backoff, connect
 * chips). The render gate asserts the layout survives that, which is the point.
 */

import type { Page, Route } from '@playwright/test';
import * as fx from './fixtures.js';

const JSON_HEADERS = {
  'content-type': 'application/json',
  'access-control-allow-origin': '*',
  'access-control-allow-headers': '*',
  'access-control-allow-methods': 'GET,POST,OPTIONS',
};

function json(route: Route, body: unknown): Promise<void> {
  return route.fulfill({ status: 200, headers: JSON_HEADERS, body: JSON.stringify(body) });
}

/** Resolve a gateway pathname (+query) to a fixture, or null if unmocked. */
function route_for(pathname: string): unknown | undefined {
  if (pathname === '/board/stats') return fx.boardStats;
  if (pathname === '/board/state') return fx.boardState;
  if (pathname === '/board/tokens-by-day') return fx.tokensByDay;
  if (pathname === '/board/session-token') return fx.sessionToken;
  if (pathname === '/v1/scout/status') return fx.scoutStatus;
  if (pathname === '/v1/scout/history') return fx.scoutHistory;
  if (pathname === '/v1/docker/status') return fx.dockerStatus;
  if (pathname === '/v1/git/activity') return fx.gitActivity;
  if (pathname === '/v1/escalations') return fx.escalations;
  if (pathname === '/v1/calendar/jobs/upcoming') return fx.upcomingJobs;
  if (pathname === '/v1/suno/tracks') return fx.sunoTracks;
  return undefined;
}

export async function installGatewayMock(page: Page): Promise<void> {
  await page.route('http://127.0.0.1:8766/**', async (route) => {
    const url = new URL(route.request().url());
    const { pathname } = url;

    // Preflight: always allow.
    if (route.request().method() === 'OPTIONS') {
      return route.fulfill({ status: 204, headers: JSON_HEADERS, body: '' });
    }

    // The embedded crew board iframe (?embed=1) → seeded stub HTML.
    if (pathname === '/board') {
      return route.fulfill({
        status: 200,
        headers: { 'content-type': 'text/html', 'access-control-allow-origin': '*' },
        body: fx.boardEmbedHtml,
      });
    }

    // Board mutations / POSTs → 200 ok envelope (gate never mutates, but be safe).
    if (route.request().method() === 'POST') {
      return json(route, { ok: true });
    }

    const body = route_for(pathname);
    if (body !== undefined) return json(route, body);

    // Audio / media / unknown reads → empty 200 so nothing throws.
    return route.fulfill({ status: 200, headers: { 'access-control-allow-origin': '*' }, body: '' });
  });
}
