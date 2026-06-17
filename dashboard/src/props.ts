/**
 * props.ts — Lively Wallpaper LivelyProperties integration.
 *
 * Lively calls `livelyPropertyListener(name, value)` once on load for each
 * property (init pass), then again whenever the user changes a value via
 * the Customise panel (right-click wallpaper → Customise).
 *
 * We handle:
 *   - `deviceToken`  (textbox) — Bearer for /v1/* routes; stored in-memory
 *                                + localStorage for persistence across reloads
 *                                (Lively also persists the property on disk).
 *   - `pollInterval` (slider, ms, default 3000) — passed to scheduler
 *   - `motionEnabled` (checkbox, default true) — controls CSS animations
 *
 * Exported: `getDeviceToken()` for gateway.ts, `onPropertyChange` for
 * scheduler and motion modules.
 */

import { setBearerToken } from './gateway.js';

const LS_KEY_TOKEN = 'hive_device_token';
const LS_KEY_POLL  = 'hive_poll_interval_ms';

// ─── Property state ───────────────────────────────────────────────────────────

let _pollIntervalMs = 3_000;
let _motionEnabled  = true;

type PropChangeListener = (name: string, value: string) => void;
const propListeners: PropChangeListener[] = [];

export function onPropertyChange(fn: PropChangeListener): void {
  propListeners.push(fn);
}

export function getPollIntervalMs(): number {
  return _pollIntervalMs;
}

export function isMotionEnabled(): boolean {
  return _motionEnabled;
}

// ─── Init from localStorage ───────────────────────────────────────────────────

function initFromStorage(): void {
  const storedToken = localStorage.getItem(LS_KEY_TOKEN);
  if (storedToken) {
    setBearerToken(storedToken);
    updateTokenStatusUI(true);
  }

  const storedPoll = localStorage.getItem(LS_KEY_POLL);
  if (storedPoll) {
    const ms = parseInt(storedPoll, 10);
    if (!isNaN(ms) && ms >= 500) {
      _pollIntervalMs = ms;
    }
  }
}

function updateTokenStatusUI(hasToken: boolean): void {
  const el = document.getElementById('token-status');
  if (el) {
    el.textContent = hasToken ? 'yes' : 'no';
    el.style.color = hasToken ? 'var(--green)' : 'var(--red)';
  }
}

// ─── Property handler ─────────────────────────────────────────────────────────

function handleProperty(name: string, value: string): void {
  switch (name) {
    case 'deviceToken': {
      const token = value.trim();
      if (token) {
        setBearerToken(token);
        localStorage.setItem(LS_KEY_TOKEN, token);
        updateTokenStatusUI(true);
      } else {
        setBearerToken(null);
        localStorage.removeItem(LS_KEY_TOKEN);
        updateTokenStatusUI(false);
      }
      break;
    }
    case 'pollInterval': {
      const ms = parseInt(value, 10);
      if (!isNaN(ms) && ms >= 500) {
        _pollIntervalMs = ms;
        localStorage.setItem(LS_KEY_POLL, String(ms));
      }
      break;
    }
    case 'motionEnabled': {
      _motionEnabled = value === 'true' || value === '1';
      document.documentElement.classList.toggle('no-motion', !_motionEnabled);
      break;
    }
    default:
      break;
  }

  // Notify other modules
  for (const fn of propListeners) {
    try {
      fn(name, value);
    } catch (err) {
      console.error('[props] listener error', err);
    }
  }
}

// ─── Lively global hook ───────────────────────────────────────────────────────

declare global {
  interface Window {
    livelyPropertyListener: (name: string, value: string) => void;
  }
}

window.livelyPropertyListener = handleProperty;

// ─── Bootstrap ───────────────────────────────────────────────────────────────

initFromStorage();
