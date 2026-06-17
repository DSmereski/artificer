/**
 * rendergate/playwright.config.ts — the multi-resolution render gate.
 *
 * Serves the PROD build via `vite preview` (so BASE_URL is the gateway origin
 * 127.0.0.1:8766, which the mock intercepts — same as the real Lively wallpaper
 * context). `npm run rendergate` builds first, then runs this.
 */

import { defineConfig, devices } from '@playwright/test';

const PORT = 4317;

export default defineConfig({
  testDir: '.',
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  reporter: [['list']],
  use: {
    baseURL: `http://localhost:${PORT}`,
    ...devices['Desktop Chrome'],
  },
  webServer: {
    command: `npm run preview -- --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env['CI'],
    timeout: 60_000,
  },
});
