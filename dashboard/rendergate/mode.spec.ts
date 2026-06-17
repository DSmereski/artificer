/**
 * rendergate/mode.spec.ts — P4 hybrid mode gate.
 *
 * Asserts the ambient ⇄ focus contract on the wide template:
 *   • default is ambient: read-only (mutating controls hidden), calm chrome;
 *   • clicking the ◐ toggle enters focus: FOCUS chrome, mutating controls shown,
 *     any heavy live panel un-suspended;
 *   • Esc returns to ambient.
 *
 * Mode state is observable without a live gateway, so this runs against the
 * same deterministic mock as the render gate.
 */

import { test, expect } from '@playwright/test';
import { installGatewayMock } from './mock.js';

test.beforeEach(async ({ page }) => {
  await installGatewayMock(page);
  await page.setViewportSize({ width: 1920, height: 1080 });
  // Each Playwright test gets a fresh context → localStorage starts empty,
  // so the default mode is ambient without any explicit reset.
  await page.goto('/');
  await page.waitForFunction(() => {
    const g = document.getElementById('dashboard-grid');
    return !!g && g.querySelectorAll('.dashboard-cell').length >= 4;
  }, undefined, { timeout: 15_000 });
});

test('defaults to ambient: read-only + calm chrome', async ({ page }) => {
  await expect(page.locator('body')).toHaveClass(/mode-ambient/);
  await expect(page.locator('body')).not.toHaveClass(/mode-focus/);
  // Mutating controls (pause/task/goal) are hidden in ambient.
  await expect(page.locator('#act-pause')).toBeHidden();
  await expect(page.locator('#act-task')).toBeHidden();
  await expect(page.locator('#act-goal')).toBeHidden();
  await expect(page.locator('#act-mode')).toContainText('Ambient');
});

test('clicking ◐ enters focus: interactive + FOCUS chrome', async ({ page }) => {
  await page.locator('#act-mode').click();
  await expect(page.locator('body')).toHaveClass(/mode-focus/);
  // Mutating controls become available.
  await expect(page.locator('#act-pause')).toBeVisible();
  await expect(page.locator('#act-task')).toBeVisible();
  await expect(page.locator('#act-mode')).toContainText('Focus');
  // No heavy panel is left veiled in focus.
  await expect(page.locator('.dashboard-cell[data-suspended="1"]')).toHaveCount(0);
});

test('Esc returns focus → ambient', async ({ page }) => {
  await page.locator('#act-mode').click();
  await expect(page.locator('body')).toHaveClass(/mode-focus/);
  await page.keyboard.press('Escape');
  await expect(page.locator('body')).toHaveClass(/mode-ambient/);
  await expect(page.locator('#act-pause')).toBeHidden();
});

test('mode persists across reload', async ({ page }) => {
  await page.locator('#act-mode').click();
  await expect(page.locator('body')).toHaveClass(/mode-focus/);
  await page.reload();
  await page.waitForFunction(() => !!document.getElementById('dashboard-grid'), undefined, { timeout: 15_000 });
  await expect(page.locator('body')).toHaveClass(/mode-focus/);
});
