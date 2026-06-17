/**
 * rendergate/render.spec.ts — the render gate.
 *
 * For each of the 5 locked targets, loads the built dashboard against the
 * deterministic gateway mock and asserts the layout invariants from the
 * REDESIGN plan's verifier:
 *   (a) no visible panel overflows the grid,
 *   (b) no black void — the core panels (kpi + crew-board-full) are present,
 *       in their designed slots, and a template-appropriate minimum of panels
 *       render,
 *   (c) the page itself never grows a scrollbar (wallpaper must fit exactly).
 *
 * Runs against `vite preview` of the prod build (see playwright.config.ts).
 */

import { test, expect, type Page } from '@playwright/test';
import { installGatewayMock } from './mock.js';

interface Target {
  label: string;
  w: number;
  h: number;
  tpl: 'ultrawide' | 'wide' | 'portrait';
  minPanels: number;
}

// Mirrors pickTemplate(): portrait if h>w; ultrawide if ratio>=2.4; else wide.
const TARGETS: Target[] = [
  { label: 'ultrawide-5440x1440', w: 5440, h: 1440, tpl: 'ultrawide', minPanels: 7 },
  { label: 'wide-1920x1080', w: 1920, h: 1080, tpl: 'wide', minPanels: 5 },
  { label: 'wide-2560x1440', w: 2560, h: 1440, tpl: 'wide', minPanels: 5 },
  { label: 'wide-3840x2160', w: 3840, h: 2160, tpl: 'wide', minPanels: 5 },
  { label: 'portrait-1440x2560', w: 1440, h: 2560, tpl: 'portrait', minPanels: 3 },
];

// Always-on panels (relevant whenever the gateway is up and not gaming).
const CORE_PANELS = ['kpi', 'crew-board-full'];

interface CellReport {
  id: string;
  w: number;
  h: number;
  gridArea: string;
  overflowR: number; // px the cell extends past the grid's right edge (>0 = bad)
  overflowB: number; // px past the bottom
  underflowL: number; // px the cell starts before the grid's left edge (>0 = bad)
  underflowT: number;
  opacity: number;   // computed opacity of the cell (0 = invisible/black panel)
  childOpacity: number; // min opacity among direct children (frozen entry anim → 0)
}
interface Report {
  template: string;
  cells: CellReport[];
  pageScrollOverflowX: number; // documentElement.scrollWidth - innerWidth
  pageScrollOverflowY: number;
}

async function collect(page: Page): Promise<Report> {
  return page.evaluate(() => {
    const grid = document.getElementById('dashboard-grid');
    const gr = grid?.getBoundingClientRect() ?? { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight };
    const tpl = (() => {
      const a = grid ? getComputedStyle(grid).gridTemplateAreas : '';
      // first area token of each template, used only for a sanity label
      if (a.includes('term term term term term term term term')) return 'ultrawide';
      if (a.includes('"kpi"')) return 'portrait';
      return a ? 'wide' : 'none';
    })();

    const cells: CellReport[] = [];
    const nodes = grid ? Array.from(grid.querySelectorAll<HTMLElement>('.dashboard-cell')) : [];
    for (const el of nodes) {
      if (el.style.display === 'none' || el.offsetParent === null) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) continue;
      const childOps = Array.from(el.children).map((c) => parseFloat(getComputedStyle(c).opacity || '1'));
      cells.push({
        id: el.dataset['pluginId'] ?? el.id,
        w: Math.round(r.width),
        h: Math.round(r.height),
        gridArea: getComputedStyle(el).gridArea,
        overflowR: Math.round(r.right - gr.right),
        overflowB: Math.round(r.bottom - gr.bottom),
        underflowL: Math.round(gr.left - r.left),
        underflowT: Math.round(gr.top - r.top),
        opacity: parseFloat(getComputedStyle(el).opacity || '1'),
        childOpacity: childOps.length ? Math.min(...childOps) : 1,
      });
    }
    return {
      template: tpl,
      cells,
      pageScrollOverflowX: document.documentElement.scrollWidth - window.innerWidth,
      pageScrollOverflowY: document.documentElement.scrollHeight - window.innerHeight,
    };
  });
}

for (const t of TARGETS) {
  test(`render gate · ${t.label}`, async ({ page }) => {
    await installGatewayMock(page);
    await page.setViewportSize({ width: t.w, height: t.h });
    await page.goto('/');

    // Wait for the layout to mount real cells (data arrives via the mock poll).
    await page.waitForFunction(
      (min) => {
        const g = document.getElementById('dashboard-grid');
        if (!g) return false;
        const vis = Array.from(g.querySelectorAll<HTMLElement>('.dashboard-cell'))
          .filter((el) => el.style.display !== 'none' && el.offsetParent !== null);
        return vis.length >= min;
      },
      t.minPanels,
      { timeout: 15_000 },
    );
    // Settle past the staggered cellIn entry animation (max nth-child delay
    // 540ms + 360ms duration) so late cells have faded in, plus uPlot/iframe.
    await page.waitForTimeout(1300);

    const rep = await collect(page);
    const ids = rep.cells.map((c) => c.id);

    // (template) the right screen-class is active.
    expect(rep.template, `expected ${t.tpl} template`).toBe(t.tpl);

    // (b1) core panels present.
    for (const core of CORE_PANELS) {
      expect(ids, `${core} missing on ${t.label}`).toContain(core);
    }

    // (b2) no black void — a template-appropriate panel count.
    expect(rep.cells.length, `too few panels on ${t.label}`).toBeGreaterThanOrEqual(t.minPanels);

    // (a) no panel overflows the grid (3px slack for sub-pixel/borders).
    for (const c of rep.cells) {
      expect(c.overflowR, `${c.id} overflows right on ${t.label}`).toBeLessThanOrEqual(3);
      expect(c.overflowB, `${c.id} overflows bottom on ${t.label}`).toBeLessThanOrEqual(3);
      expect(c.underflowL, `${c.id} spills left on ${t.label}`).toBeLessThanOrEqual(3);
      expect(c.underflowT, `${c.id} spills top on ${t.label}`).toBeLessThanOrEqual(3);
      // each visible cell has real area
      expect(c.w, `${c.id} zero width`).toBeGreaterThan(8);
      expect(c.h, `${c.id} zero height`).toBeGreaterThan(8);
      // each visible cell actually PAINTS (not frozen at opacity:0 → black panel).
      expect(c.opacity, `${c.id} cell invisible (opacity 0)`).toBeGreaterThan(0.05);
      expect(c.childOpacity, `${c.id} content invisible (opacity 0)`).toBeGreaterThan(0.05);
    }

    // (c) the wallpaper itself never scrolls.
    expect(rep.pageScrollOverflowX, `page scrolls X on ${t.label}`).toBeLessThanOrEqual(2);
    expect(rep.pageScrollOverflowY, `page scrolls Y on ${t.label}`).toBeLessThanOrEqual(2);
  });
}
