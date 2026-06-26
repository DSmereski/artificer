/**
 * audio-viz-bg.ts — Full-screen audio-reactive background visualizer.
 *
 * Renders a full-viewport spectrum behind the dashboard panels. Reads
 * frequency data from the same AnalyserNode the in-panel Suno visualizer
 * uses — the MediaElementSource can only be created once, so we reuse the
 * existing analyser rather than creating a new one.
 *
 * Positioning: position:fixed; inset:0; z-index:0 — above the body
 * background / fx-backdrop (z-index:0, earlier in DOM), but below the
 * dashboard-grid and panels (z-index:1). The canvas is appended to
 * document.body AFTER the fx-backdrop is inserted (motion.ts inserts it
 * as body's first child; this canvas comes after), so at the same z-level
 * the paint order places the canvas on top of the backdrop.
 *
 * Performance:
 *   - Single rAF loop; loop only runs when audio is playing.
 *   - Pauses when document is hidden.
 *   - Suspends entirely when the render tier is 'gaming' (frees GPU for game).
 *   - Canvas sized to devicePixelRatio, capped at 2 (matches weather-fx.ts).
 *   - pointer-events:none — never intercepts clicks on the dashboard.
 *
 * Usage: import as a side-effect module from main.ts (like weather-fx.ts).
 *   import './audio-viz-bg.js';
 */

import { getMusicAnalyser, getSunoAudio } from './panels/suno.js';

// ─── State ────────────────────────────────────────────────────────────────────

let _canvas: HTMLCanvasElement | null = null;
let _ctx: CanvasRenderingContext2D | null = null;
let _raf: number | null = null;

/** DPR-adjusted logical dimensions (CSS pixels). */
let _W = 0;
let _H = 0;
let _DPR = 1;

/** True when the render tier is 'gaming' — we suspend to free the GPU. */
let _suspended = false;

/** Fade state: 0 = fully transparent, 1 = fully opaque. */
let _alpha = 0;
const FADE_STEP_IN  = 0.04;  // per-frame fade-in speed (~25 frames to full)
const FADE_STEP_OUT = 0.03;  // slightly slower fade-out

// Reusable typed array — allocated once, reused every frame.
let _freqData: Uint8Array<ArrayBuffer> = new Uint8Array(64);

// ─── Canvas setup ─────────────────────────────────────────────────────────────

function _resize(): void {
  if (!_canvas) return;
  _DPR = Math.min(window.devicePixelRatio || 1, 2);
  _W = window.innerWidth;
  _H = window.innerHeight;
  _canvas.width  = Math.floor(_W * _DPR);
  _canvas.height = Math.floor(_H * _DPR);
  _canvas.style.width  = `${_W}px`;
  _canvas.style.height = `${_H}px`;
  if (_ctx) _ctx.setTransform(_DPR, 0, 0, _DPR, 0, 0);
}

// ─── CSS variable helpers ──────────────────────────────────────────────────────

function _cssVar(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/** Parse a hex colour string like '#c07840' into [r, g, b]. */
function _hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  if (h.length < 6) return [128, 128, 128];
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ];
}

// ─── Drawing ─────────────────────────────────────────────────────────────────

/**
 * Draw one frame of the full-screen spectrum.
 *
 * Visual design: mirrored bar spectrum (left half = ascending, right half =
 * descending mirror) using the theme's copper and amber accent colours at low
 * opacity so the panels above remain readable. A soft radial glow underlies
 * the bars on loud passages.
 */
function _draw(analyser: AnalyserNode, targetAlpha: number): void {
  if (!_ctx || _W === 0 || _H === 0) return;

  // Resize freq buffer if the analyser's fft size changed since last frame.
  const binCount = analyser.frequencyBinCount;
  if (_freqData.length !== binCount) {
    _freqData = new Uint8Array(binCount) as Uint8Array<ArrayBuffer>;
  }
  analyser.getByteFrequencyData(_freqData);

  // Advance fade alpha toward target.
  if (_alpha < targetAlpha) {
    _alpha = Math.min(targetAlpha, _alpha + FADE_STEP_IN);
  } else if (_alpha > targetAlpha) {
    _alpha = Math.max(targetAlpha, _alpha - FADE_STEP_OUT);
  }

  // Skip drawing when fully faded out.
  _ctx.clearRect(0, 0, _W, _H);
  if (_alpha <= 0.005) return;

  // Theme accent colours: copper (low energy) → amber (high energy).
  const copperHex = _cssVar('--hex-copper', '#c07840');
  const amberHex  = _cssVar('--hex-amber',  '#e0a030');
  const [cr, cg, cb] = _hexToRgb(copperHex);
  const [ar, ag, ab] = _hexToRgb(amberHex);

  // Number of visible bars (half the viewport → mirrored).
  const bars     = 80;
  const barW     = _W / bars;
  const halfBars = Math.ceil(bars / 2);

  // Compute average loudness for the glow layer.
  let loudnessSum = 0;
  for (let i = 0; i < binCount; i++) loudnessSum += (_freqData[i] ?? 0);
  const avgLoudness = loudnessSum / binCount / 255; // 0–1

  // Soft radial glow on loud passages, faded by outer alpha.
  if (avgLoudness > 0.05) {
    const glowR = _W * 0.55;
    const glow = _ctx.createRadialGradient(_W / 2, _H, 0, _W / 2, _H, glowR);
    const glowA = _alpha * avgLoudness * 0.18;
    glow.addColorStop(0,   `rgba(${ar},${ag},${ab},${glowA.toFixed(3)})`);
    glow.addColorStop(0.5, `rgba(${cr},${cg},${cb},${(glowA * 0.4).toFixed(3)})`);
    glow.addColorStop(1,   'rgba(0,0,0,0)');
    _ctx.fillStyle = glow;
    _ctx.fillRect(0, 0, _W, _H);
  }

  // Draw bars (bottom-anchored, mirrored left/right).
  const barGap = Math.max(1, Math.round(barW * 0.15));

  for (let i = 0; i < halfBars; i++) {
    // Map bar index → frequency bin (use the lower half of spectrum for better
    // visual shape — bass on center, treble toward edges after mirror).
    const binIdx = Math.floor((i / halfBars) * (binCount * 0.75));
    const v      = (_freqData[binIdx] ?? 0) / 255; // 0–1
    const bh     = Math.max(2, v * _H * 0.65);     // max 65% of screen height

    // Interpolate colour: copper at rest → amber at peak.
    const r  = Math.round(cr + (ar - cr) * v);
    const gv = Math.round(cg + (ag - cg) * v);
    const b  = Math.round(cb + (ab - cb) * v);
    const barAlpha = _alpha * (0.25 + v * 0.45);  // 0.25–0.70

    // Left mirror bar (center outward → halfBars position on the right half).
    const xLeft  = (_W / 2) - (i + 1) * barW;
    const xRight = (_W / 2) + i * barW;
    const barActualW = Math.max(1, barW - barGap);

    _ctx.fillStyle = `rgba(${r},${gv},${b},${barAlpha.toFixed(3)})`;
    _ctx.fillRect(xLeft,  _H - bh, barActualW, bh);
    _ctx.fillRect(xRight, _H - bh, barActualW, bh);
  }
}

// ─── Animation loop ───────────────────────────────────────────────────────────

function _isPlaying(): boolean {
  // Ask the shared audio element (created in initSunoPlayer) whether it is
  // actively playing. getSunoAudio() returns null until initSunoPlayer() runs,
  // which always precedes any 'play' event, so this is safe.
  const audio = getSunoAudio();
  if (!audio) return false;
  return !audio.paused && !audio.ended && audio.readyState >= 2;
}

function _tick(): void {
  _raf = requestAnimationFrame(_tick);

  const analyser = getMusicAnalyser();

  if (!analyser || _suspended) {
    // No analyser yet or gaming tier — fade out canvas then stop looping.
    if (_alpha > 0.005) {
      // Drive alpha toward 0 and clear canvas.
      _alpha = Math.max(0, _alpha - FADE_STEP_OUT);
      if (_ctx) _ctx.clearRect(0, 0, _W, _H);
    } else {
      // Fully faded — cancel the loop; a 'play' event will restart it.
      cancelAnimationFrame(_raf);
      _raf = null;
    }
    return;
  }

  // Determine if audio is actually playing.
  const playing = _isPlaying();
  _draw(analyser, playing ? 1 : 0);

  // Once fully faded out after pause, stop the loop to save resources.
  if (!playing && _alpha <= 0.005) {
    cancelAnimationFrame(_raf);
    _raf = null;
  }
}

function _startLoop(): void {
  if (_raf !== null || _suspended || document.hidden) return;
  _tick();
}

// ─── Public surface (late-bind from suno events) ──────────────────────────────

/**
 * Called when the suno player audio starts playing.
 * Kicks off the rAF loop if it isn't running.
 */
export function onAudioVizPlay(): void {
  _startLoop();
}

/**
 * Called when the render tier changes to 'gaming' (suspend) or away from it
 * (resume). Matched to the motionOnTierChange pattern in main.ts.
 */
export function onAudioVizTierChange(tier: string): void {
  const wasSuspended = _suspended;
  _suspended = tier === 'gaming' || tier === 'offline';

  if (!_suspended && wasSuspended) {
    // Resuming from suspension — restart loop if audio is playing.
    _startLoop();
  }
  // If now suspended, the loop's next tick will fade out and self-cancel.
}

// ─── Init (side-effect on import) ─────────────────────────────────────────────

function _init(): void {
  _canvas = document.createElement('canvas');
  _canvas.id = 'audio-viz-bg';
  // position:fixed; inset:0 — full viewport.
  // z-index:0 — same numeric level as #fx-backdrop but painted after it in DOM
  // order, so it renders on top of the backdrop and BELOW the dashboard-grid
  // (which has position:relative; z-index:1). pointer-events:none so clicks
  // always reach the panel layer above.
  _canvas.style.cssText =
    'position:fixed;inset:0;pointer-events:none;z-index:0;display:block;';
  document.body.appendChild(_canvas);

  const c = _canvas.getContext('2d');
  if (!c) return;
  _ctx = c;

  _resize();
  window.addEventListener('resize', _resize);

  // Pause the loop when the tab is hidden (Lively minimised etc.).
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      if (_raf !== null) { cancelAnimationFrame(_raf); _raf = null; }
    } else {
      _startLoop();
    }
  });

  // Wire to the audio element's play/pause events so we start/stop responsively.
  // The audio element is created lazily in initSunoPlayer (called from main.ts),
  // so we use event delegation on document for 'play' events.
  document.addEventListener('play', (e) => {
    const target = e.target;
    if (target instanceof HTMLAudioElement) {
      _startLoop();
    }
  }, /* capture */ true);

  // On pause/ended we don't need to do anything extra: the loop's _isPlaying()
  // check will return false and the loop will fade out and self-cancel.
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _init);
} else {
  _init();
}
