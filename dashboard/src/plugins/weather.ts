/**
 * plugins/weather.ts — Open-Meteo weather PanelPlugin (v-Next P3).
 *
 * A NEW opt-in module. Adds a current-conditions + short-forecast panel backed
 * by Open-Meteo. Open-Meteo is KEYLESS — there is NO API key, token, or secret
 * anywhere in this file. Geocoding + forecast are both public, no-auth GETs.
 *
 * Per-instance settings (drive the gear form via `settingsSchema`):
 *   - location  (string)  — city name to geocode (default "Atlanta")
 *   - units     (select)  — 'fahrenheit' | 'celsius' (default fahrenheit)
 *
 * Multi-instance: settings are read per cell from the `data-instance-id`
 * attribute the layout sets on the cell element before mount() (see
 * layout/freeform-apply.ts). A second weather instance with location='Seattle'
 * resolves a different instanceId → different settings → shows Seattle while
 * the first shows Atlanta. Per-cell view state is kept in a WeakMap keyed by the
 * cell element so the two instances never clobber each other.
 *
 * Back-compat: a fresh user with no weather instance sees no change — the panel
 * only ever renders when the Module Manager creates an instance of it.
 */

import { register } from './registry.js';
import type { PanelPlugin, RelevanceResult, Rect } from './contract.js';
import type { SystemState, RenderBudget } from '../state/types.js';
import { getInstance } from './instances.js';
import { escHtml } from '../format.js';

// ─── Public Open-Meteo hosts (keyless, no secret) ────────────────────────────

const GEOCODE_HOST = 'https://geocoding-api.open-meteo.com/v1/search';
const FORECAST_HOST = 'https://api.open-meteo.com/v1/forecast';

const FETCH_TIMEOUT_MS = 8_000;
const FORECAST_DAYS = 4;

// ─── Settings types ───────────────────────────────────────────────────────────

export type WeatherUnits = 'fahrenheit' | 'celsius';

interface WeatherSettings {
  location: string;
  units: WeatherUnits;
}

const DEFAULT_SETTINGS: WeatherSettings = {
  location: 'Atlanta',
  units: 'fahrenheit',
};

// ─── WMO weather_code → icon/label (pure, exported for tests) ─────────────────

/**
 * Map a WMO weather interpretation code to a display icon + short label.
 * Covers the common code ranges; unknown codes fall back to a neutral cloud.
 * @see https://open-meteo.com/en/docs (WMO Weather interpretation codes)
 */
export function weatherCodeToInfo(code: number): { icon: string; label: string } {
  if (code === 0) return { icon: '☀', label: 'Clear' };
  if (code >= 1 && code <= 3) return { icon: '⛅', label: 'Partly cloudy' };
  if (code === 45 || code === 48) return { icon: '🌫', label: 'Fog' };
  if (code >= 51 && code <= 67) return { icon: '🌧', label: 'Rain' };
  if (code >= 71 && code <= 77) return { icon: '❄', label: 'Snow' };
  if (code >= 80 && code <= 82) return { icon: '🌦', label: 'Showers' };
  if (code >= 95 && code <= 99) return { icon: '⛈', label: 'Thunderstorm' };
  return { icon: '☁', label: 'Cloudy' };
}

// ─── URL builders (pure, exported for tests) ─────────────────────────────────

/** Build the keyless Open-Meteo geocoding URL for a city name. */
export function buildGeocodeUrl(name: string): string {
  const params = new URLSearchParams({
    name,
    count: '1',
    format: 'json',
  });
  return `${GEOCODE_HOST}?${params.toString()}`;
}

/** Build the keyless Open-Meteo forecast URL for a lat/lon + unit system. */
export function buildForecastUrl(lat: number, lon: number, units: WeatherUnits): string {
  const params = new URLSearchParams({
    latitude: String(lat),
    longitude: String(lon),
    current: 'temperature_2m,weather_code,wind_speed_10m',
    daily: 'weather_code,temperature_2m_max,temperature_2m_min',
    temperature_unit: units,
    wind_speed_unit: 'mph',
    timezone: 'auto',
    forecast_days: String(FORECAST_DAYS),
  });
  return `${FORECAST_HOST}?${params.toString()}`;
}

// ─── View-model + parser (pure, exported for tests) ──────────────────────────

export interface ForecastDay {
  /** ISO date string (YYYY-MM-DD). */
  date: string;
  /** Day-of-week label, e.g. "Mon". */
  dow: string;
  icon: string;
  label: string;
  high: number;
  low: number;
}

export interface WeatherView {
  currentTemp: number;
  windSpeed: number;
  icon: string;
  label: string;
  days: ForecastDay[];
}

/** Short weekday label from an ISO date (UTC-safe). */
function _dowLabel(isoDate: string): string {
  const ms = Date.parse(`${isoDate}T00:00:00Z`);
  if (!Number.isFinite(ms)) return '--';
  return new Date(ms).toLocaleDateString('en-US', { weekday: 'short', timeZone: 'UTC' });
}

/**
 * Shape a raw Open-Meteo forecast response into a clean view-model.
 * Returns null when the response is missing the current block (treated as error).
 */
export function parseForecast(json: unknown): WeatherView | null {
  if (json == null || typeof json !== 'object') return null;
  const obj = json as Record<string, unknown>;

  const current = obj['current'] as Record<string, unknown> | undefined;
  if (!current || typeof current['temperature_2m'] !== 'number') return null;

  const curCode = typeof current['weather_code'] === 'number' ? current['weather_code'] : -1;
  const curInfo = weatherCodeToInfo(curCode);

  const days: ForecastDay[] = [];
  const daily = obj['daily'] as Record<string, unknown> | undefined;
  if (daily) {
    const times = Array.isArray(daily['time']) ? (daily['time'] as string[]) : [];
    const codes = Array.isArray(daily['weather_code']) ? (daily['weather_code'] as number[]) : [];
    const highs = Array.isArray(daily['temperature_2m_max'])
      ? (daily['temperature_2m_max'] as number[])
      : [];
    const lows = Array.isArray(daily['temperature_2m_min'])
      ? (daily['temperature_2m_min'] as number[])
      : [];

    for (let i = 0; i < times.length; i++) {
      const code = typeof codes[i] === 'number' ? codes[i]! : -1;
      const info = weatherCodeToInfo(code);
      days.push({
        date: times[i]!,
        dow: _dowLabel(times[i]!),
        icon: info.icon,
        label: info.label,
        high: typeof highs[i] === 'number' ? Math.round(highs[i]!) : NaN,
        low: typeof lows[i] === 'number' ? Math.round(lows[i]!) : NaN,
      });
    }
  }

  return {
    currentTemp: Math.round(current['temperature_2m'] as number),
    windSpeed: typeof current['wind_speed_10m'] === 'number'
      ? Math.round(current['wind_speed_10m'])
      : NaN,
    icon: curInfo.icon,
    label: curInfo.label,
    days,
  };
}

// ─── Per-cell view state ──────────────────────────────────────────────────────

type CellPhase = 'loading' | 'ready' | 'error';

interface CellState {
  instanceId: string | null;
  /** Settings snapshot the last successful fetch was keyed on. */
  lastKey: string;
  ticks: number;
  phase: CellPhase;
  view: WeatherView | null;
  errorMsg: string;
  suspended: boolean;
  /** Monotonic request id so a stale in-flight fetch can't overwrite a newer one. */
  reqSeq: number;
}

// Per-cell state, keyed by the cell element. WeakMap = no leak when cells drop.
const _cells = new WeakMap<HTMLElement, CellState>();

const _REFRESH_TICKS = 600; // ~10 min at a ~1s state tick

// ─── Settings resolution ──────────────────────────────────────────────────────

/** Read the merged settings for a cell from its data-instance-id (P0 wiring). */
function _resolveSettings(el: HTMLElement): WeatherSettings {
  const instanceId = el.dataset['instanceId'] ?? null;
  if (instanceId) {
    const inst = getInstance(instanceId);
    if (inst) {
      const raw = inst.settings ?? {};
      const location =
        typeof raw['location'] === 'string' && raw['location'].trim()
          ? (raw['location'] as string).trim()
          : DEFAULT_SETTINGS.location;
      const units = raw['units'] === 'celsius' ? 'celsius' : 'fahrenheit';
      return { location, units };
    }
  }
  return { ...DEFAULT_SETTINGS };
}

/** A stable key for "have the inputs that drive the query changed?". */
function _settingsKey(s: WeatherSettings): string {
  return `${s.location.toLowerCase()}|${s.units}`;
}

// ─── Mount ────────────────────────────────────────────────────────────────────

function mount(el: HTMLElement): void {
  const state: CellState = {
    instanceId: el.dataset['instanceId'] ?? null,
    lastKey: '',
    ticks: 0,
    phase: 'loading',
    view: null,
    errorMsg: '',
    suspended: false,
    reqSeq: 0,
  };
  _cells.set(el, state);

  el.style.background = 'var(--panel, #14110f)';
  el.style.borderRadius = '10px';
  el.style.padding = '8px 10px';
  el.style.display = 'flex';
  el.style.flexDirection = 'column';

  const settings = _resolveSettings(el);
  el.innerHTML = `
    <div class="panel-header" style="margin-bottom:6px">
      <span class="panel-label">WEATHER</span>
    </div>
    <div class="weather-body" style="flex:1;min-height:0;display:flex;flex-direction:column;justify-content:center"></div>
  `;

  _render(el, state, settings);
  void _fetchWeather(el, state, settings);
}

// ─── Fetch ────────────────────────────────────────────────────────────────────

async function _fetchJson(url: string): Promise<unknown> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(url, { signal: controller.signal });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

/** Geocode → forecast → parse → render. Errors are caught + shown gracefully. */
async function _fetchWeather(
  el: HTMLElement,
  state: CellState,
  settings: WeatherSettings,
): Promise<void> {
  const seq = ++state.reqSeq;
  state.lastKey = _settingsKey(settings);

  try {
    // 1. Geocode the city name → lat/lon.
    const geo = (await _fetchJson(buildGeocodeUrl(settings.location))) as {
      results?: Array<{ latitude: number; longitude: number; name?: string }>;
    };
    if (seq !== state.reqSeq) return; // a newer fetch superseded this one
    const first = geo?.results?.[0];
    if (!first || typeof first.latitude !== 'number' || typeof first.longitude !== 'number') {
      _fail(el, state, 'City not found');
      return;
    }

    // 2. Forecast for that lat/lon.
    const raw = await _fetchJson(
      buildForecastUrl(first.latitude, first.longitude, settings.units),
    );
    if (seq !== state.reqSeq) return;

    const view = parseForecast(raw);
    if (!view) {
      _fail(el, state, 'Weather unavailable');
      return;
    }

    state.phase = 'ready';
    state.view = view;
    state.errorMsg = '';
    _render(el, state, settings);
  } catch {
    if (seq !== state.reqSeq) return;
    _fail(el, state, 'Weather unavailable');
  }
}

function _fail(el: HTMLElement, state: CellState, msg: string): void {
  state.phase = 'error';
  state.errorMsg = msg;
  _render(el, state, _resolveSettings(el));
}

// ─── Render ───────────────────────────────────────────────────────────────────

function _unitSymbol(units: WeatherUnits): string {
  return units === 'celsius' ? '°C' : '°F';
}

function _render(el: HTMLElement, state: CellState, settings: WeatherSettings): void {
  const body = el.querySelector<HTMLElement>('.weather-body');
  if (!body) return;

  const loc = escHtml(settings.location);

  if (state.phase === 'loading') {
    body.innerHTML =
      `<p class="offline-state" style="color:var(--faint)">Loading weather for ${loc}…</p>`;
    return;
  }

  if (state.phase === 'error') {
    body.innerHTML =
      `<p class="offline-state" style="color:var(--faint)">${escHtml(state.errorMsg)} (${loc})</p>`;
    return;
  }

  const view = state.view;
  if (!view) {
    body.innerHTML = `<p class="offline-state" style="color:var(--faint)">No data (${loc})</p>`;
    return;
  }

  const u = _unitSymbol(settings.units);
  const wind = Number.isFinite(view.windSpeed) ? `${view.windSpeed} mph` : '--';

  const forecastRow = view.days
    .slice(0, FORECAST_DAYS)
    .map((d) => {
      const hi = Number.isFinite(d.high) ? `${d.high}°` : '--';
      const lo = Number.isFinite(d.low) ? `${d.low}°` : '--';
      return `
        <div style="display:flex;flex-direction:column;align-items:center;gap:2px;flex:1">
          <span style="font-size:10px;color:var(--faint)">${escHtml(d.dow)}</span>
          <span style="font-size:16px" title="${escHtml(d.label)}">${d.icon}</span>
          <span style="font-size:10px;color:var(--ink)">${hi}</span>
          <span style="font-size:10px;color:var(--faint)">${lo}</span>
        </div>`;
    })
    .join('');

  body.innerHTML = `
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
      <span style="font-size:34px;line-height:1" title="${escHtml(view.label)}">${view.icon}</span>
      <div style="display:flex;flex-direction:column">
        <span style="font-size:24px;font-weight:600;color:var(--ink)">${view.currentTemp}${u}</span>
        <span style="font-size:11px;color:var(--faint)">${escHtml(view.label)} · ${loc}</span>
        <span style="font-size:10px;color:var(--faint)">wind ${wind}</span>
      </div>
    </div>
    <div style="display:flex;gap:4px;border-top:1px solid var(--line, #2a2620);padding-top:6px">
      ${forecastRow}
    </div>
  `;
}

// ─── Update ───────────────────────────────────────────────────────────────────

function update(_state: SystemState, _budget: RenderBudget): void {
  // Drive every mounted weather cell. We discover cells from the DOM so each
  // instance (with its own data-instance-id) refreshes against its own settings.
  const cells = document.querySelectorAll<HTMLElement>('[data-plugin-id="weather"]');
  cells.forEach((el) => {
    const cs = _cells.get(el);
    if (!cs || cs.suspended) return;

    const settings = _resolveSettings(el);
    const key = _settingsKey(settings);

    // Settings changed (location/units) → re-fetch immediately.
    if (key !== cs.lastKey) {
      cs.ticks = 0;
      cs.phase = 'loading';
      _render(el, cs, settings);
      void _fetchWeather(el, cs, settings);
      return;
    }

    // Periodic refresh.
    cs.ticks++;
    if (cs.ticks >= _REFRESH_TICKS) {
      cs.ticks = 0;
      void _fetchWeather(el, cs, settings);
    }
  });
}

// ─── Relevance ────────────────────────────────────────────────────────────────

function relevance(state: SystemState): RelevanceResult {
  // Opt-in ambient module: don't fight for space in the gaming tier.
  if (state.tier === 'gaming') return { priority: 0, size: 'hidden' };
  return { priority: 35, size: 'sm' };
}

// ─── Resize / suspend / resume ────────────────────────────────────────────────

function onResize(_rect: Rect): void {
  // Layout is flex/auto — nothing to recompute.
}

function suspend(): void {
  // Mark all known cells suspended (pause periodic refresh).
  const cells = document.querySelectorAll<HTMLElement>('[data-plugin-id="weather"]');
  cells.forEach((el) => {
    const cs = _cells.get(el);
    if (cs) cs.suspended = true;
  });
}

function resume(): void {
  const cells = document.querySelectorAll<HTMLElement>('[data-plugin-id="weather"]');
  cells.forEach((el) => {
    const cs = _cells.get(el);
    if (cs) {
      cs.suspended = false;
      cs.ticks = _REFRESH_TICKS; // refresh on next tick
    }
  });
}

// ─── Plugin definition ────────────────────────────────────────────────────────

const weatherPlugin: PanelPlugin = {
  id: 'weather',
  title: 'WEATHER',
  dataSources: [{ kind: 'state' }], // external API; no gateway poll wiring
  relevance,
  mount,
  update,
  onResize,
  suspend,
  resume,
  defaultSettings: { ...DEFAULT_SETTINGS },
  settingsSchema: {
    fields: [
      {
        key: 'location',
        label: 'Location',
        type: 'string',
        default: 'Atlanta',
        hint: 'City name (e.g. Seattle, London)',
      },
      {
        key: 'units',
        label: 'Units',
        type: 'select',
        default: 'fahrenheit',
        options: [
          { value: 'fahrenheit', label: '°F' },
          { value: 'celsius', label: '°C' },
        ],
      },
    ],
  },
};

register(weatherPlugin);
export { weatherPlugin };
