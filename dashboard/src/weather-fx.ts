/**
 * weather-fx.ts — ambient rain / snow weather overlay for the wallpaper.
 *
 * A faithful port of the Matching-on-the-8s persistent drizzle + snow overlays.
 * A single full-viewport canvas (pointer-events:none) sits above the panels and
 * paints theme-colored precipitation. Two top-bar toggles (🌧 / ❄) turn each
 * layer on; both are OFF by default and persisted in localStorage.
 *
 * Colors come entirely from the active theme's --fx-* CSS custom properties
 * (emitted as hex by gen-dashboard-css.mjs from the canonical token library), so
 * switching theme recolors the weather live. Themes whose --fx-style is "matrix"
 * (code-fall / code-red) render the rain layer as falling Matrix glyphs.
 *
 * Self-initializes on import (side-effect module, like props.js / pause.js).
 */

type Rng = () => number;

const REDUCED = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
const MATRIX_GLYPHS =
  '0101ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆ'.split('');

type Drop = { x: number; y: number; len: number; spd: number; wind: number; alt: boolean };
type Flake = { x: number; y: number; r: number; spd: number; drift: number; phase: number; alt: boolean };
type Col = { x: number; y: number; spd: number; seed: number };

let canvas: HTMLCanvasElement;
let ctx: CanvasRenderingContext2D;
let W = 0, H = 0, DPR = 1;
let rainOn = false;
let snowOn = false;
let raf = 0;
let drops: Drop[] = [];
let flakes: Flake[] = [];
let cols: Col[] = [];

// Active theme fx colors, refreshed on theme change.
const fx = {
  rain: '#9fd', rain2: '#9fd',
  snow: '#fff', snow2: '#fff',
  glow: '#cff', glow2: '#cff',
  matrix: false,
};

function readVar(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function refreshColors(): void {
  fx.rain = readVar('--fx-rain', '#9fd');
  fx.rain2 = readVar('--fx-rain-2', fx.rain);
  fx.snow = readVar('--fx-snow', '#fff');
  fx.snow2 = readVar('--fx-snow-2', fx.snow);
  fx.glow = readVar('--fx-snow-glow', '#cff');
  fx.glow2 = readVar('--fx-snow-glow-2', fx.glow);
  fx.matrix = readVar('--fx-style', 'drops') === 'matrix';
}

const rand: Rng = Math.random;

function resize(): void {
  DPR = Math.min(window.devicePixelRatio || 1, 2);
  // The canvas is position:fixed inset:0, so its rendered size IS the full
  // viewport span. Use clientWidth/Height — window.innerWidth under-reported on
  // the multi-monitor wallpaper (gave ~1 monitor, so rain covered only 1/3 of
  // the 5440-wide screen). Do NOT override canvas.style.width/height: that
  // shrank the element back to innerWidth — the bug. Let inset:0 span it.
  W = canvas.clientWidth || document.documentElement.clientWidth || window.innerWidth;
  H = canvas.clientHeight || document.documentElement.clientHeight || window.innerHeight;
  canvas.width = Math.floor(W * DPR);
  canvas.height = Math.floor(H * DPR);
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  spawn();
}

function spawn(): void {
  const area = W * H;
  const nDrops = Math.min(520, Math.round(area / 7000));
  const nFlakes = Math.min(220, Math.round(area / 9000));
  // Matrix columns are placed at x = i*16, so the count MUST cover the full
  // width — a 120 cap only reached 1920px (~1/3 of the 5120 wallpaper). Scale to
  // width with a generous ceiling for ultrawide spans.
  const nCols = Math.min(420, Math.round(W / 16));

  drops = Array.from({ length: nDrops }, () => ({
    x: rand() * W, y: rand() * H,
    len: 8 + rand() * 14, spd: 320 + rand() * 360,
    wind: 16 + rand() * 26, alt: rand() < 0.5,
  }));
  flakes = Array.from({ length: nFlakes }, () => ({
    x: rand() * W, y: rand() * H,
    r: 1.1 + rand() * 2.4, spd: 26 + rand() * 44,
    drift: 12 + rand() * 26, phase: rand() * Math.PI * 2, alt: rand() < 0.5,
  }));
  cols = Array.from({ length: nCols }, (_v, i) => ({
    x: i * 16 + 4, y: rand() * H, spd: 90 + rand() * 150, seed: Math.floor(rand() * 9999),
  }));
}

function drawRainStreaks(dt: number): void {
  ctx.lineWidth = 1.1;
  ctx.lineCap = 'round';
  for (const d of drops) {
    d.y += d.spd * dt;
    d.x += d.wind * dt;
    if (d.y > H + d.len) { d.y = -d.len; d.x = rand() * W; }
    if (d.x > W + 20) d.x = -20;
    ctx.globalAlpha = 0.38;
    ctx.strokeStyle = d.alt ? fx.rain : fx.rain2;
    ctx.beginPath();
    ctx.moveTo(d.x, d.y);
    ctx.lineTo(d.x - d.wind * 0.03, d.y - d.len);
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
}

function drawMatrix(dt: number): void {
  ctx.font = '14px JetBrains Mono, monospace';
  ctx.textBaseline = 'top';
  for (const c of cols) {
    c.y += c.spd * dt;
    if (c.y > H + 160) { c.y = -rand() * 200; c.seed = Math.floor(rand() * 9999); }
    const trail = 9;
    for (let i = 0; i < trail; i++) {
      const gy = c.y - i * 15;
      if (gy < -15 || gy > H) continue;
      const lead = i === 0;
      ctx.globalAlpha = lead ? 0.95 : (1 - i / trail) * 0.5;
      ctx.fillStyle = lead ? fx.rain : fx.rain2;
      if (lead) { ctx.shadowColor = fx.rain; ctx.shadowBlur = 8; } else { ctx.shadowBlur = 0; }
      const g = MATRIX_GLYPHS[(c.seed + i * 7 + ((c.y / 15) | 0)) % MATRIX_GLYPHS.length];
      ctx.fillText(g, c.x, gy);
    }
  }
  ctx.shadowBlur = 0;
  ctx.globalAlpha = 1;
}

function drawSnow(dt: number, tSec: number): void {
  for (const f of flakes) {
    f.y += f.spd * dt;
    f.x += Math.sin(tSec * 0.6 + f.phase) * f.drift * dt;
    if (f.y > H + 4) { f.y = -4; f.x = rand() * W; }
    if (f.x > W + 4) f.x = -4; else if (f.x < -4) f.x = W + 4;
    ctx.globalAlpha = 0.7;
    ctx.fillStyle = f.alt ? fx.snow : fx.snow2;
    ctx.shadowColor = f.alt ? fx.glow : fx.glow2;
    ctx.shadowBlur = 6;
    ctx.beginPath();
    ctx.arc(f.x, f.y, f.r, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.shadowBlur = 0;
  ctx.globalAlpha = 1;
}

let last = 0;
function frame(ts: number): void {
  raf = requestAnimationFrame(frame);
  if (last === 0) last = ts;
  let dt = (ts - last) / 1000;
  last = ts;
  if (dt > 0.05) dt = 0.05; // clamp after tab-away
  ctx.clearRect(0, 0, W, H);
  const tSec = ts / 1000;
  if (rainOn) { if (fx.matrix) drawMatrix(dt); else drawRainStreaks(dt); }
  if (snowOn) drawSnow(dt, tSec);
}

function start(): void {
  if (raf || REDUCED) { if (REDUCED) renderStatic(); return; }
  last = 0;
  raf = requestAnimationFrame(frame);
}
function stop(): void {
  if (raf) cancelAnimationFrame(raf);
  raf = 0;
  ctx.clearRect(0, 0, W, H);
}

// Reduced-motion: one calm static frame instead of animation.
function renderStatic(): void {
  ctx.clearRect(0, 0, W, H);
  if (snowOn) {
    for (const f of flakes) {
      ctx.globalAlpha = 0.5; ctx.fillStyle = f.alt ? fx.snow : fx.snow2;
      ctx.beginPath(); ctx.arc(f.x, f.y, f.r, 0, Math.PI * 2); ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
}

function sync(): void {
  const anyOn = rainOn || snowOn;
  canvas.style.display = anyOn ? 'block' : 'none';
  if (!anyOn) { stop(); return; }
  if (REDUCED) renderStatic(); else start();
}

function wireButton(id: string, key: string, get: () => boolean, set: (v: boolean) => void): void {
  const btn = document.getElementById(id);
  if (!btn) return;
  const paint = () => btn.classList.toggle('fx-on', get());
  try { set(localStorage.getItem(key) === '1'); } catch { /* default off */ }
  paint();
  btn.addEventListener('click', () => {
    set(!get());
    try { localStorage.setItem(key, get() ? '1' : '0'); } catch { /* ignore */ }
    paint();
    sync();
  });
}

function init(): void {
  canvas = document.createElement('canvas');
  canvas.id = 'weather-fx';
  canvas.style.cssText =
    'position:fixed;inset:0;pointer-events:none;z-index:3;display:none;';
  document.body.appendChild(canvas);
  const c = canvas.getContext('2d');
  if (!c) return;
  ctx = c;

  refreshColors();
  resize();
  // The wallpaper host may span the canvas to the full multi-monitor width a few
  // frames AFTER load (no 'resize' event fires for that), so re-measure shortly
  // after init — otherwise the backing store stays at the initial (narrow) size.
  requestAnimationFrame(() => resize());
  setTimeout(() => resize(), 800);
  window.addEventListener('resize', resize);
  window.addEventListener('hive-theme-change', () => { refreshColors(); if (REDUCED) renderStatic(); });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stop();
    else sync();
  });

  wireButton('act-rain', 'hive.fx.rain', () => rainOn, (v) => { rainOn = v; });
  wireButton('act-snow', 'hive.fx.snow', () => snowOn, (v) => { snowOn = v; });
  sync();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
