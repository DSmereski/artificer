/**
 * alerts.ts — CC2 alerting. When a new escalation opens (or a build fails),
 * grab attention: flash the top bar red, play a short synthesized stinger
 * (no audio asset needed), duck the Suno music, and raise a desktop toast.
 *
 * Edge-triggered: fires only on a RISING edge (new escalation appears), not
 * every poll. Clears the red flash when escalations return to zero.
 *
 * DOM/Audio-guarded so a node test import is inert.
 */

import { duckSuno } from './panels/suno.js';
import type { EscalationList } from './types.js';

let _prevOpen = 0;
let _prevSlugs = new Set<string>();
let _audioCtx: AudioContext | null = null;

// ─── Synthesized stinger (two-tone, WebAudio — no asset) ──────────────────────

function _playStinger(): void {
  if (typeof window === 'undefined') return;
  try {
    const Ctx =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext?: typeof AudioContext })
        .webkitAudioContext;
    if (!Ctx) return;
    if (!_audioCtx) _audioCtx = new Ctx();
    const ctx = _audioCtx;
    const now = ctx.currentTime;
    // Two descending tones — an alert "da-dum".
    const tones = [
      { f: 880, t: 0.0, d: 0.18 },
      { f: 587, t: 0.16, d: 0.26 },
    ];
    for (const { f, t, d } of tones) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'triangle';
      osc.frequency.value = f;
      gain.gain.setValueAtTime(0.0001, now + t);
      gain.gain.exponentialRampToValueAtTime(0.22, now + t + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + t + d);
      osc.connect(gain).connect(ctx.destination);
      osc.start(now + t);
      osc.stop(now + t + d + 0.02);
    }
  } catch {
    // best effort — no stinger is fine
  }
}

// ─── Top-bar flash ──────────────────────────────────────────────────────────────

function _setTopbarAlert(on: boolean): void {
  if (typeof document === 'undefined') return;
  document.getElementById('topbar')?.classList.toggle('alerting', on);
}

// ─── Desktop toast ────────────────────────────────────────────────────────────

function _toast(title: string, body: string): void {
  if (typeof Notification === 'undefined') return;
  try {
    if (Notification.permission === 'granted') {
      new Notification(title, { body });
    } else if (Notification.permission !== 'denied') {
      void Notification.requestPermission().then((p) => {
        if (p === 'granted') new Notification(title, { body });
      });
    }
  } catch {
    // ignore
  }
}

// ─── Public: feed escalations each poll ───────────────────────────────────────

/**
 * Call on every escalation poll. Detects a new escalation (rising edge) and
 * fires the alert once; keeps the top bar red while any are open.
 */
export function trackEscalations(list: EscalationList | null): void {
  const escs = list?.escalations ?? [];
  const open = escs.length;
  const slugs = new Set(escs.map((e) => e.slug));

  // A genuinely new escalation = a slug we hadn't seen, or count rose.
  const isNew =
    open > _prevOpen || escs.some((e) => !_prevSlugs.has(e.slug));

  if (isNew && open > 0) {
    const top = escs[0];
    _setTopbarAlert(true);
    _playStinger();
    duckSuno(2000);
    _toast(
      'Hive — escalation',
      top ? `${top.title || top.slug}: ${top.reason || 'needs attention'}` : 'A task escalated.',
    );
  }

  if (open === 0) _setTopbarAlert(false);

  _prevOpen = open;
  _prevSlugs = slugs;
}
