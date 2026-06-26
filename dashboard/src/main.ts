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
import './weather-fx.js';
import './audio-viz-bg.js';

// ─── F1: Design-system fx layer + motion governor ────────────────────────────
import './styles/fx.css';
import { initMotionGovernor, onTierChange as motionOnTierChange } from './styles/motion.js';
import { onAudioVizTierChange } from './audio-viz-bg.js';
import { installWallpaperInputDedupe } from './util/input-dedupe.js';

// ─── Plugin barrel: triggers all self-registrations ──────────────────────────
import './plugins/index.js';

import { all as allPlugins, enabled as enabledPlugins, isPanelEnabled } from './plugins/registry.js';
import { createStore } from './state/store.js';
import { wireSources, onBoardPoll, onScoutPoll, onEscalationPoll, onGatewayUp, setProjectFilter } from './state/sources.js';
import { buildSystemState } from './state/derive.js';
import { initLayoutApplier, applyTemplate, isPanelVisible, getCellEl } from './layout/apply.js';
import { resolveTemplate, openTemplateSelector, onLayoutOverrideChange, getLayoutOverride } from './layout/template-select.js';
import { applyTopbarVisibility } from './layout/monitor-config.js';
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
import { setFullBoardNudge, reloadBoardFrame } from './plugins/crew-board-full.js';
import { initCommandSurface, reflectBoardPaused } from './topbar/commands.js';
import { initBoardSwitcher, getActiveBoard, onBoardChange } from './topbar/board-switcher.js';
import { openModuleManager, setModuleManagerChangeCallback } from './plugins/module-manager.js';
import { trackEscalations } from './alerts.js';
import { onSystemScout } from './plugins/system.js';
import { onDockerStatus } from './plugins/docker.js';
import {
  logAction,
  onGitActivity,
  onActivityNeedsYouBoard,
  onActivityNeedsYouEscalations,
  setActivityNeedsYouRefresh,
} from './plugins/activity-feed.js';
import { onContentBoard } from './plugins/content-gallery.js';
import { getMode, isFocus, setMode, onModeChange, isHeavyLive } from './state/mode.js';
// ── P1: Free-form layout + presets ─────────────────────────────────────────
import { isFreeformActive } from './layout/presets.js';
import {
  initFreeformApplier,
  applyFreeformLayout,
  teardownFreeformApplier,
} from './layout/freeform-apply.js';
import { setPresetChangeCallback } from './layout/preset-controls.js';
// ── DL: Desktop windowed layout ─────────────────────────────────────────────
import {
  isDesktopActive,
  initDesktopLayout,
  applyDesktopLayout,
  saveDesktopRects,
  isDesktopLocked,
  lockDesktop,
  unlockDesktop,
} from './layout/desktop.js';
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

  if (dot)   dot.className   = `live-dot${state.gatewayUp ? '' : ' offline'}`;
  if (label) {
    label.textContent = state.gatewayUp ? 'live' : 'offline';
    label.className   = `live-label${state.gatewayUp ? '' : ' offline'}`;
  }
  if (chip) {
    // board-state-chip kept hidden in F2 layout — pulse word carries this info.
    chip.style.display = 'none';
    chip.className = `board-state-chip ${state.activity}`;
  }
  if (cost) cost.textContent = state.counts.costUsd > 0
    ? `$${state.counts.costUsd.toFixed(2)}`
    : '--';
  if (esc) {
    esc.textContent = state.escalations.open > 0 ? `esc:▲${state.escalations.open}` : '';
    esc.className   = `topbar-esc-badge${state.escalations.open > 0 ? ' active' : ''}`;
  }

  const pulse = document.getElementById('topbar-pulse');
  // F2.1: determine the pulse word + apply health-pill state class.
  const pill  = document.getElementById('topbar-health-pill');
  let pulseWord = 'IDLE';
  let pulseColor = 'var(--green)';
  let pillState: 'is-urgent' | 'is-active' | '' = '';

  if (!state.gatewayUp) {
    pulseWord = 'OFFLINE'; pulseColor = 'var(--red)'; pillState = 'is-urgent';
  } else if (state.tier === 'gaming') {
    pulseWord = 'GAMING';  pulseColor = 'var(--dim)';  pillState = '';
  } else if (state.tasks.building.length > 0) {
    pulseWord = 'BUILDING'; pulseColor = 'var(--amber)'; pillState = 'is-active';
  } else if (state.escalations.open > 0) {
    pulseWord = 'NEEDS YOU'; pulseColor = 'var(--red)'; pillState = 'is-urgent';
  }

  if (pulse) { pulse.textContent = pulseWord; pulse.style.color = pulseColor; }
  if (pill)  {
    pill.classList.remove('is-urgent', 'is-active');
    if (pillState) pill.classList.add(pillState);
  }
}

// ─── Main engine loop ─────────────────────────────────────────────────────────

async function init(): Promise<void> {
  initProbeInput();
  // Kill Lively's double-keystroke echo on all text inputs (typing "test" → "tteesstt").
  installWallpaperInputDedupe();

  // ── F1: Motion governor — init before first paint so data-motion is set ──
  initMotionGovernor();

  // ── Container + layout applier ──
  const container = _initContainer();
  initLayoutApplier(container);
  // P1: also wire the free-form applier (no-op until a preset is active).
  initFreeformApplier(container);
  // DL: desktop windowed layout (activated by dash:desktopMode=1).
  initDesktopLayout(container);

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

  // P2 v-Next: board switcher. Renders into the dedicated #topbar-board-switcher
  // container (F2 layout). Falls back to #topbar for back-compat.
  const _boardSwitcherEl =
    document.getElementById('topbar-board-switcher') ??
    document.getElementById('topbar');
  if (_boardSwitcherEl) {
    void initBoardSwitcher(_boardSwitcherEl);
  }
  // #211: honor this monitor's top-bar on/off flag (per ?win) at startup.
  applyTopbarVisibility();
  // Apply persisted project selection immediately before the first poll.
  setProjectFilter(getActiveBoard());
  onBoardChange(() => {
    setProjectFilter(getActiveBoard());
    store.emit();         // filter client-side immediately
    reloadBoardFrame();   // re-point the embedded board iframe at the new project
    void pollBoard();     // also refresh from gateway
  });

  // F2.2: ⋯ More popover — toggle on the trigger button, close on outside click.
  {
    const moreBtn = document.getElementById('act-more');
    const morePop = document.getElementById('topbar-more-popover');
    if (moreBtn && morePop) {
      // Portal out of #topbar: its backdrop-filter traps position:fixed children
      // inside its stacking context, so the popover painted BEHIND the grid.
      // As a body child it's truly viewport-fixed and its z-index wins.
      document.body.appendChild(morePop);
      const _place = (): void => {
        const r = moreBtn.getBoundingClientRect();
        morePop.style.top = `${Math.round(r.bottom + 6)}px`;
        morePop.style.right = `${Math.round(window.innerWidth - r.right)}px`;
        morePop.style.left = 'auto';
      };
      moreBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        const open = !morePop.hidden;
        if (!open) _place();          // about to show → anchor under the ⋯ button
        morePop.hidden = open;
        moreBtn.setAttribute('aria-expanded', open ? 'false' : 'true');
      });
      document.addEventListener('click', (e) => {
        if (!morePop.hidden && !moreBtn.contains(e.target as Node) && !morePop.contains(e.target as Node)) {
          morePop.hidden = true;
          moreBtn.setAttribute('aria-expanded', 'false');
        }
      });
    }
  }

  // Top-bar "Board" button → open the crew board web page (the gateway serves
  // the full kanban at /board) in the system browser.
  document.getElementById('act-board')?.addEventListener('click', () => {
    window.open('http://127.0.0.1:8766/board', '_blank', 'noopener');
  });

  // P1: Legacy preset-change callback (kept for module-manager compatibility).
  setPresetChangeCallback(() => store.emit());
  // Layout button → template selector overlay.
  document.getElementById('act-presets')?.addEventListener('click', () => {
    openTemplateSelector(() => store.emit());
  });
  // Re-apply layout when the override changes (e.g. on reload from localStorage).
  onLayoutOverrideChange(() => {
    lastLayoutSig = '';  // force re-apply even if template name is the same
    if (_lastState) _applyLayout(_lastState);
  });

  // v-Next: Module Manager button — add/remove/duplicate modules + per-instance
  // settings. A module change re-emits so the layout re-applies (visible in
  // free-form mode; in template mode the instance is stored until you switch).
  setModuleManagerChangeCallback(() => store.emit());
  document.getElementById('act-modules')?.addEventListener('click', () => {
    openModuleManager();
  });

  // CC2: approving a review task from the Activity feed → re-poll to reflect.
  setActivityNeedsYouRefresh(() => void pollBoard());

  // DL1: ambient/focus is collapsed into ONE mode — the toggle button + its
  // hotkey are removed (David: "I don't like the ambient/focus mode, just
  // combine them"). The mode stays at its default; nothing switches it.
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && isFocus()) { setMode('ambient'); return; }
  });

  // DL4: lock toggle. Show the button when desktop mode is active.
  {
    const lockBtn = document.getElementById('act-lock');
    if (lockBtn) {
      if (isDesktopActive()) lockBtn.style.display = '';
      lockBtn.addEventListener('click', () => {
        if (isDesktopLocked()) {
          unlockDesktop();
          lockBtn.textContent = '🔓 Unlock';
          lockBtn.classList.remove('is-locked');
        } else {
          lockDesktop();
          lockBtn.textContent = '🔒 Lock';
          lockBtn.classList.add('is-locked');
        }
      });
    }
  }

  // DL4: persist desktop rects on page exit.
  window.addEventListener('pagehide', () => saveDesktopRects());
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') saveDesktopRects();
  });

  // DL1: ambient/focus toggle removed — one mode. _reflectMode still sets the
  // body.mode-* class (CSS hooks) for the pinned default mode; the button-update
  // lines below are null-safe no-ops now that the button is gone.
  const _reflectMode = (): void => {
    document.body.classList.toggle('mode-focus', isFocus());
    document.body.classList.toggle('mode-ambient', !isFocus());
    const _mb = document.getElementById('act-mode');
    if (_mb) {
      _mb.textContent = getMode() === 'focus' ? '◓ Focus' : '◐ Ambient';
      _mb.classList.toggle('is-focus', isFocus());
    }
  };
  onModeChange(() => { _reflectMode(); store.emit(); });
  // Reflect initial chrome before the first data tick.
  _reflectMode();

  // ── Layout (P0: hand-designed templates per screen-class; no auto-packer) ──
  // Re-apply the grid only when the screen-class OR the visible panel set
  // changes (resize, focus toggle, a panel appears/disappears). Panel CONTENT
  // still updates every tick via the plugin.update() loop below — this is why
  // the grid no longer reshuffles as data streams in.
  let lastLayoutSig = '';
  let _lastState: SystemState | null = null;

  let _lastLayoutMode: 'template' | 'freeform' | 'desktop' = 'template';

  function _applyLayout(state: SystemState): void {
    // DL: desktop windowed mode takes highest priority (opt-in via localStorage flag).
    if (isDesktopActive()) {
      if (_lastLayoutMode !== 'desktop') {
        // Tear down previous mode cleanly.
        if (_lastLayoutMode === 'freeform') teardownFreeformApplier();
        container.style.cssText = '';
        lastLayoutSig = '';
        _lastLayoutMode = 'desktop';
      }
      const layoutId = getLayoutOverride();
      applyDesktopLayout(allPlugins(), state, layoutId);
      return;
    }

    // P1: branch between free-form and template layout.
    if (isFreeformActive()) {
      if (_lastLayoutMode !== 'freeform') {
        lastLayoutSig = '';
        _lastLayoutMode = 'freeform';
      }
      applyFreeformLayout(enabledPlugins(), state);
      return;
    }

    // Template mode (the default, back-compat path).
    if (_lastLayoutMode === 'freeform') {
      teardownFreeformApplier();
      container.style.cssText = '';
      lastLayoutSig = '';
      _lastLayoutMode = 'template';
    }
    if (_lastLayoutMode === 'desktop') {
      container.style.cssText = '';
      lastLayoutSig = '';
      _lastLayoutMode = 'template';
    }

    const tpl = resolveTemplate(window.innerWidth, window.innerHeight);
    // DL1: focusedId() panel-zoom removed. Panels always use their template slot.
    const plugins = allPlugins();
    const visible = plugins
      .filter((p) => tpl.slots[p.id] != null && isPanelEnabled(p.id) && isPanelVisible(p, state))
      .map((p) => p.id);
    const sig = `${tpl.name}|${getLayoutOverride()}|${visible.sort().join(',')}`;
    if (sig !== lastLayoutSig) {
      lastLayoutSig = sig;
      applyTemplate(tpl, plugins, state, null);
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

    // F1: update motion governor from current tier
    motionOnTierChange(state.tier);
    onAudioVizTierChange(state.tier);

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
      // The selection is a PROJECT slug, not a board id — fetch the full board
      // and filter client-side (the gateway's ?board= param is a different axis).
      const [stats, state] = await Promise.all([
        getBoardStats().catch((e) => { console.warn('[poll:board] stats', e); return null; }),
        getBoardState().catch((e) => { console.warn('[poll:board] state', e); return null; }),
      ]);
      const online = stats !== null || state !== null;
      onGatewayUp(online);
      onBoardPoll(stats, state);
      if (state) reflectBoardPaused(!!state.paused);
      // Project filter applies to the visible board-task surfaces too, so the
      // activity feed's review rows + content gallery reflect the selection.
      const proj = getActiveBoard();
      const boardTasks = proj
        ? (state?.tasks ?? []).filter((t) => t.project_slug === proj)
        : (state?.tasks ?? []);
      if (state) onActivityNeedsYouBoard(boardTasks);
      if (state) onContentBoard(boardTasks);
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
      onActivityNeedsYouEscalations(escs);
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
