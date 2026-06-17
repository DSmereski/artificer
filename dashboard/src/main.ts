/**
 * main.ts — Hive Command Center v2 entry point.
 *
 * v2 architecture:
 *   store → layout engine + registry → plugins (auto-placed)
 *
 * Slimmed to: import plugins (self-register) → create store →
 *   subscribe → engine computes layout → plugins mount/update.
 *
 * All poll wiring now comes from plugins' dataSources declarations
 * and the governor driving scheduler.setInterval().
 */

// ─── Side-effect imports: Lively integration ──────────────────────────────────
import './props.js';
import './pause.js';

// ─── Plugin barrel: triggers all self-registrations ──────────────────────────
import './plugins/index.js';

import { all as allPlugins, enabled as enabledPlugins } from './plugins/registry.js';
import { createStore } from './state/store.js';
import { wireSources, onBoardPoll, onScoutPoll, onEscalationPoll, onGatewayUp } from './state/sources.js';
import { buildSystemState } from './state/derive.js';
import { initLayoutApplier, applyTemplate, isPanelVisible, getCellEl } from './layout/apply.js';
import { pickTemplate } from './layout/templates.js';
import { governor } from './resource/governor.js';
import {
  getBoardStats,
  getBoardState,
  pingGateway,
  getScoutStatus,
  getDockerStatus,
  getGitActivity,
  getEscalations,
  getUpcomingJobs,
  sunoTracks,
} from './gateway.js';
import { createScheduler } from './scheduler.js';
import { onPauseStateChange } from './pause.js';
import { initProbeInput } from './probe_input.js';
import { onBoardStats as telemetryOnStats } from './plugins/telemetry.js';
import { onAgendaData } from './plugins/agenda.js';
import { onSunoTracks } from './plugins/suno.js';
import { initSunoPlayer } from './panels/suno.js';
import { setFullBoardNudge } from './plugins/crew-board-full.js';
import { initCommandSurface, reflectBoardPaused } from './topbar/commands.js';
import { onNeedsYouBoard, onNeedsYouEscalations, setNeedsYouRefresh } from './plugins/needs-you.js';
import { trackEscalations } from './alerts.js';
import { onSystemScout } from './plugins/system.js';
import { onDockerStatus } from './plugins/docker.js';
import { logAction } from './plugins/actions-log.js';
import { onGitActivity } from './plugins/git-activity.js';
import { onContentBoard } from './plugins/content-gallery.js';
import { focusedId, setFocusNudge, toggleFocus, clearFocus } from './layout/focus.js';
import { getMode, isFocus, setMode, toggleMode, onModeChange, isHeavyLive } from './state/mode.js';
import { prependTickerEvent } from './panels/telemetry.js';
import { getBearerToken } from './gateway.js';
import { onBoardEvent } from './state/sources.js';
import { createEventWS } from './ws.js';
import { shapeFrameToTicker, shapeBoardFrameToTicker } from './ws_frames.js';
import type { SystemState } from './state/types.js';

// ─── Layout container ─────────────────────────────────────────────────────────

function _initContainer(): HTMLElement {
  // Use the existing #dashboard-grid if present, else create a fullscreen container
  let container = document.getElementById('dashboard-grid') as HTMLElement | null;
  if (!container) {
    container = document.createElement('div');
    container.id = 'dashboard-grid';
    container.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;overflow:hidden;';
    document.body.appendChild(container);
  }
  return container;
}

// ─── Topbar ───────────────────────────────────────────────────────────────────

function _updateTopbar(state: SystemState): void {
  const dot   = document.getElementById('live-dot');
  const label = document.getElementById('live-label');
  const chip  = document.getElementById('board-state-chip');
  const cost  = document.getElementById('topbar-cost');
  const esc   = document.getElementById('topbar-esc-badge');
  const tier  = document.getElementById('topbar-tier');

  if (dot)   dot.className   = `live-dot${state.gatewayUp ? '' : ' offline'}`;
  if (label) { label.textContent = state.gatewayUp ? 'live' : 'offline';
                label.className  = `live-label${state.gatewayUp ? '' : ' offline'}`; }
  if (chip) {
    chip.textContent = state.activity;
    chip.className   = `board-state-chip ${state.activity}`;
  }
  if (cost)  cost.textContent  = state.counts.costUsd > 0
    ? `$${state.counts.costUsd.toFixed(2)}`
    : '--';
  if (esc) {
    esc.textContent = state.escalations.open > 0 ? `esc:▲${state.escalations.open}` : '';
    esc.className   = `topbar-esc-badge${state.escalations.open > 0 ? ' active' : ''}`;
  }
  if (tier) {
    const tierLabel: Record<string, string> = {
      idle: 'idle', busy: 'busy', gaming: 'gaming', offline: 'offline',
    };
    tier.textContent = tierLabel[state.tier] ?? state.tier;
    tier.setAttribute('data-tier', state.tier);
  }
}

// ─── Main engine loop ─────────────────────────────────────────────────────────

async function init(): Promise<void> {
  initProbeInput();

  // ── Container + layout applier ──
  const container = _initContainer();
  initLayoutApplier(container);

  // ── Initial state (offline, no data yet) ──
  const initialState = buildSystemState({
    gatewayUp:       false,
    tasks:           [],
    stats:           null,
    escalationsOpen: 0,
    gpus:            [],
    cpuPct:          0,
    ramPct:          0,
    tier:            'offline',
  });

  const store = createStore(initialState);
  wireSources(store);

  // Let the full-board toggle force a re-layout on click (not a state change).
  setFullBoardNudge(() => store.emit());

  // Top-bar Suno player lives outside the adaptive grid (transport in the top
  // bar + a dropdown song navigator). Init once; the suno poll feeds it.
  initSunoPlayer();

  // CC1 command surface: top-bar Pause/Resume + Task + Goal + Ctrl+K palette.
  // refresh = immediate board re-poll so mutations reflect at once.
  initCommandSurface({ refresh: () => void pollBoard(), relayout: () => store.emit() });

  // Top-bar live clock. The standalone Clock panel is retired (it ate a grid
  // cell for one number); the time now ticks here once a second.
  const _clockEl = document.getElementById('clock');
  const _tickClock = (): void => {
    if (_clockEl) {
      _clockEl.textContent = new Date().toLocaleTimeString('en-US', { hour12: false });
    }
  };
  _tickClock();
  setInterval(_tickClock, 1000);

  // Top-bar "Board" button → open the crew board web page (the gateway serves
  // the full kanban at /board) in the system browser.
  document.getElementById('act-board')?.addEventListener('click', () => {
    window.open('http://127.0.0.1:8766/board', '_blank', 'noopener');
  });

  // CC2: the "needs you" rail approves review tasks → re-poll to reflect.
  setNeedsYouRefresh(() => void pollBoard());

  // CC5 focus mode: a toggle is a user action (not a state change), so it
  // nudges the store to re-layout. Click a panel header to focus/unfocus it;
  // Esc exits focus. Ignore clicks on header controls (buttons/inputs).
  setFocusNudge(() => store.emit());
  const grid = document.getElementById('dashboard-grid');
  grid?.addEventListener('click', (e) => {
    const target = e.target as HTMLElement | null;
    if (!target) return;
    if (target.closest('button, input, select, a, .needs-approve')) return;
    const header = target.closest('.panel-header');
    if (!header) return;
    const cell = target.closest('[data-plugin-id]') as HTMLElement | null;
    const id = cell?.dataset['pluginId'];
    if (id) toggleFocus(id);
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && focusedId()) { clearFocus(); return; }
    // P4: Esc from a clean focus deck (no CC5 panel zoomed) returns to ambient.
    if (e.key === 'Escape' && !focusedId() && isFocus()) { setMode('ambient'); return; }
    // Desktop hotkey (Lively keyboard is unreliable, so this is a convenience,
    // not the primary affordance — the ◐ button is).
    if ((e.ctrlKey || e.metaKey) && (e.key === '.' || e.key === '`')) {
      e.preventDefault();
      toggleMode();
    }
  });

  // P4 hybrid mode: the ◐ button is the PRIMARY toggle (click is forwarded by
  // Lively; keyboard is not). A mode change re-emits state so the subscribe
  // re-applies layout + mode + the update tick (terminals reconnect on focus).
  document.getElementById('act-mode')?.addEventListener('click', () => toggleMode());
  onModeChange(() => store.emit());
  // Reflect initial chrome before the first data tick.
  document.body.classList.toggle('mode-focus', isFocus());
  document.body.classList.toggle('mode-ambient', !isFocus());
  {
    const _mb = document.getElementById('act-mode');
    if (_mb) _mb.textContent = getMode() === 'focus' ? '◓ Focus' : '◐ Ambient';
  }

  // ── Layout (P0: hand-designed templates per screen-class; no auto-packer) ──
  // Re-apply the grid only when the screen-class OR the visible panel set
  // changes (resize, focus toggle, a panel appears/disappears). Panel CONTENT
  // still updates every tick via the plugin.update() loop below — this is why
  // the grid no longer reshuffles as data streams in.
  let lastLayoutSig = '';
  let _lastState: SystemState | null = null;

  function _applyLayout(state: SystemState): void {
    const tpl = pickTemplate(window.innerWidth, window.innerHeight);
    const fid = focusedId();
    const plugins = enabledPlugins();
    const visible = fid
      ? [fid]
      : plugins
          .filter((p) => tpl.slots[p.id] != null && isPanelVisible(p, state))
          .map((p) => p.id);
    const sig = `${tpl.name}|${fid ?? ''}|${visible.sort().join(',')}`;
    if (sig !== lastLayoutSig) {
      lastLayoutSig = sig;
      applyTemplate(tpl, plugins, state, fid);
    }
  }

  // P4 hybrid mode: drive heavy live renderers (terminal) on top of normal
  // visibility. In ambient they suspend (cheap 24/7 wallpaper) + show a peek
  // veil; in focus they resume live. Runs AFTER _applyLayout each tick because
  // applyTemplate resumes panels on show — this re-suspends them in ambient.
  function _applyMode(): void {
    const focus = isFocus();
    document.body.classList.toggle('mode-focus', focus);
    document.body.classList.toggle('mode-ambient', !focus);
    const btn = document.getElementById('act-mode');
    if (btn) btn.textContent = focus ? '◓ Focus' : '◐ Ambient';

    for (const p of enabledPlugins()) {
      if (!isHeavyLive(p.id)) continue;
      const el = getCellEl(p.id);
      if (!el) continue;
      const visible = el.style.display !== 'none';
      if (visible && !focus) {
        try { p.suspend?.(); } catch (err) { console.warn(`[mode] ${p.id}.suspend`, err); }
        el.setAttribute('data-suspended', '1');
      } else {
        el.removeAttribute('data-suspended');
        if (visible && focus) {
          try { p.resume?.(); } catch (err) { console.warn(`[mode] ${p.id}.resume`, err); }
        }
      }
    }
  }

  // ── Subscribe to state changes ──
  store.subscribe((state) => {
    _lastState = state;
    _updateTopbar(state);

    // Update governor cadence
    const bud = governor.budget();
    scheduler.setInterval('scout', bud.scoutMs);
    scheduler.setInterval('board', bud.boardMs);

    _applyLayout(state);
    _applyMode();

    // Update visible plugins' content every tick.
    const bud2 = governor.budget();
    for (const plugin of enabledPlugins()) {
      const el = getCellEl(plugin.id);
      if (!el || el.style.display === 'none') continue;
      // CC7 error boundary: one panel's update() throwing must not abort the rest.
      try {
        plugin.update(state, bud2);
      } catch (err) {
        console.error(`[panel] ${plugin.id}.update threw:`, err);
      }
    }
  });

  // Re-pick the template on viewport change. Lively fires no resize on setwp,
  // but the page reloads then (initial layout runs); a ResizeObserver covers
  // genuine resizes + the dev-preview at different sizes.
  const _ro = new ResizeObserver(() => {
    if (_lastState) _applyLayout(_lastState);
  });
  _ro.observe(document.body);

  // ── Scheduler + poll fns ──
  const scheduler = createScheduler();

  async function pollBoard(): Promise<void> {
    try {
      const [stats, state] = await Promise.all([
        getBoardStats().catch((e) => { console.warn('[poll:board] stats', e); return null; }),
        getBoardState().catch((e) => { console.warn('[poll:board] state', e); return null; }),
      ]);
      const online = stats !== null || state !== null;
      onGatewayUp(online);
      onBoardPoll(stats, state);
      if (state) reflectBoardPaused(!!state.paused);
      if (state) onNeedsYouBoard(state.tasks ?? []);
      if (state) onContentBoard(state.tasks ?? []);
      if (stats) telemetryOnStats(stats);
    } catch (err) {
      console.error('[poll:board]', err);
      onGatewayUp(false);
    }
  }

  async function pollScout(): Promise<void> {
    try {
      const scout = await getScoutStatus();
      onScoutPoll(scout);
      onSystemScout(scout);
    } catch (err) {
      console.warn('[poll:scout]', err);
      onScoutPoll(null);
    }
  }

  async function pollRight(): Promise<void> {
    try {
      const [escs, jobs, docker, git] = await Promise.all([
        getEscalations().catch((e) => { console.warn('[poll:esc]', e); return null; }),
        getUpcomingJobs().catch((e) => { console.warn('[poll:cal]', e); return null; }),
        getDockerStatus().catch((e) => { console.warn('[poll:docker]', e); return null; }),
        getGitActivity().catch((e) => { console.warn('[poll:git]', e); return null; }),
      ]);
      if (escs !== undefined) onEscalationPoll(escs);
      onNeedsYouEscalations(escs);
      trackEscalations(escs);
      onDockerStatus(docker);
      onGitActivity(git);
      // Agenda data fed directly to panel
      if (jobs !== undefined) onAgendaData(jobs);
    } catch (err) {
      console.warn('[poll:right]', err);
    }
  }

  async function pollSuno(): Promise<void> {
    try {
      const tracks = await sunoTracks();
      onSunoTracks(tracks);
    } catch (err) {
      console.warn('[poll:suno]', err);
    }
  }

  // Register poll jobs (intervals driven by governor via setInterval)
  scheduler.register('board', 10_000, pollBoard);
  scheduler.register('scout',  3_000, pollScout);
  scheduler.register('right', 30_000, pollRight);
  scheduler.register('suno',  60_000, pollSuno);

  // Initial gateway check
  const online = await pingGateway();
  onGatewayUp(online);

  // Initial fetch
  await scheduler.runAll();

  // Start recurring polls
  scheduler.start();

  // ── WS live event feed (Phase C) ────────────────────────────────────────────
  const eventWS = createEventWS({
    getToken: getBearerToken,
    getWsMode: () => governor.budget().ws,
    onFrame: (frame) => {
      // Feed /v1/events frames to the activity ticker
      const evt = shapeFrameToTicker(frame);
      if (evt) {
        const kind =
          evt.css?.includes('alert') || evt.css?.includes('escalation')
            ? 'alert'
            : evt.css?.includes('done') || evt.css?.includes('image')
            ? 'done'
            : 'info';
        prependTickerEvent(evt.label, kind);
        logAction(kind === 'alert' ? 'alert' : kind === 'done' ? 'done' : 'info', evt.label);
      }
    },
    onBoard: (frame) => {
      // Feed /board/events frames to ticker + nudge state
      const evt = shapeBoardFrameToTicker(frame);
      if (evt) {
        const kind =
          evt.css?.includes('alert') ? 'alert' : evt.css?.includes('moved') ? 'done' : 'info';
        prependTickerEvent(evt.label, kind);
        logAction('board', evt.label);
      }
      // Nudge board re-poll for responsive state updates
      onBoardEvent(frame.type);
    },
  });

  // Integrate with pause/resume
  onPauseStateChange((paused) => {
    if (paused) {
      scheduler.stop();
      eventWS.suspend();
      // Suspend all plugins (error-boundaried — one failure can't block others)
      for (const plugin of allPlugins()) {
        try { plugin.suspend?.(); } catch (err) { console.error(`[panel] ${plugin.id}.suspend threw:`, err); }
      }
    } else {
      eventWS.resume();
      for (const plugin of allPlugins()) {
        try { plugin.resume?.(); } catch (err) { console.error(`[panel] ${plugin.id}.resume threw:`, err); }
      }
      void scheduler.runAll().then(() => scheduler.start());
    }
  });
}

void init();
