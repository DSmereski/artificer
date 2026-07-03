# SPEC — Command Center Dashboard

Reimplementation-grade spec for the live wallpaper/dashboard app in
`dashboard/`. Written from the shipped source so a fresh implementation, in
any stack, reproduces the same architecture and behavior. Vanilla TypeScript
+ Vite; no UI framework. Rendered full-bleed (default target 5120×1440, but
responsive down to a single portrait panel) either as a browser tab or as a
live desktop-wallpaper host (Windows: Lively Wallpaper via WebView2; the app
itself has no OS dependency — see §6).

## 1. Stack

- TypeScript, Vite 6, zero UI framework. DOM built by hand (`innerHTML` +
  targeted `querySelector` updates), styled with CSS custom properties.
- Charting: `uplot` (line/area charts), `d3-force` + `d3-quadtree` (force
  graph), hand-rolled canvas sparklines/gauges.
- Terminal: `@xterm/xterm` + `@xterm/addon-fit` over a WebSocket PTY bridge.
- Tests: Vitest (unit, jsdom) + Playwright (`rendergate/`, a layout-invariant
  gate — see §7).
- No secrets, no build-time API keys. All state comes from a runtime HTTP/WS
  gateway (§2.5) at a configurable base URL.

## 2. Architecture

### 2.1 Panel/module contract

Every panel is a `PanelPlugin` object (`src/plugins/contract.ts`):

```ts
interface PanelPlugin {
  id: string;                 // kebab-case, unique
  title: string;               // header label
  dataSources: DataSourceSpec[]; // declarative: poll | ws | state (documentation only)
  relevance(state: SystemState): { priority: number; size: SizeHint; weight?: number };
  mount(el: HTMLElement): void;         // one-time DOM build, idempotent
  update(state: SystemState, budget: RenderBudget): void; // every state tick
  onResize?(rect: Rect): void;
  suspend?(): void;           // stop timers/anims when hidden/paused
  resume?(): void;
  defaultSettings?: Record<string, unknown>;   // opt-in per-instance settings
  settingsSchema?: SettingsSchema;              // drives a generated settings form
}
```

`relevance()` is a pure function of `SystemState` returning whether the panel
should show and at what size class (`hero|lg|md|sm|min|hidden`); it is used
only as a **visibility gate** in the live layout (see 2.3) — priority/size no
longer drives placement in the shipped hand-designed grid, only in the legacy
auto-packer kept for reference (`src/layout/engine.ts`, unused by `main.ts`).

**Adding a panel = one new file that calls `register({...})` as a side
effect on import, plus one export line in the barrel `src/plugins/index.ts`.**
No other core file changes. `src/plugins/registry.ts` is a `Map<id, plugin>`
filled by that side effect; it also tracks a user-disabled set persisted to
`localStorage['dash:disabledPanels']` (disabled-by-exception, so new panels
default ON).

### 2.2 Layout engine

Two coexisting systems:

1. **Template mode (default).** `src/layout/templates.ts` hand-defines a CSS
   `grid-template-areas` map per screen class — `ultrawide` (aspect ratio
   ≥ 2.4), `wide` (everything else landscape), `portrait` (taller than wide).
   `pickTemplate(w, h)` selects by viewport aspect ratio. Each template has a
   `slots: Record<panelId, areaName>` map; a panel with no slot on a given
   class is simply not shown there — not a packing failure. `src/layout/apply.ts`
   applies the template to a single `display:grid` container: it sets
   `gridTemplateColumns/Rows/Areas` once per template change, then for every
   plugin sets `el.style.gridArea = slot`, mounts the panel's cell on first
   appearance, and calls `suspend()`/`resume()` on hide/show transitions. A
   panel that throws in `mount`/`relevance` is caught and rendered as an
   inline error state — it never blocks the rest of the grid (error
   boundary, §2.6). The grid re-applies **only** when `(template name, layout
   override, sorted visible-panel-id list)` changes — not on every data tick
   — so panel content updates without the grid ever reflowing.
2. **Opt-in alternate modes**, layered on the same plugin set:
   - **Free-form** (`src/layout/freeform.ts` + `freeform-apply.ts`) — drag/
     resize any panel instance to an absolute `{x,y,w,h}`, grid-snapped,
     persisted per instance.
   - **Desktop** (`src/layout/desktop.ts`) — every panel becomes an
     independent floating, draggable, resizable window with a lock toggle;
     geometry + z-order persist to `localStorage`.
   Both are pointer/mouse-only (the wallpaper host forwards mouse, not
   keyboard) and namespaced per monitor via a `?win=<n>` query param so a
   multi-monitor deployment keeps independent layouts.

A legacy relevance-weighted auto-packer (`src/layout/engine.ts`,
`computeLayout`) ranks panels by priority, maps size hints to a 12-column
span, and bin-packs them into horizontal bands on a fixed 5120×1440 canvas.
It is fully unit-tested but **not wired into `main.ts`** — kept as a
reference implementation; a reimplementation may use either strategy, but
the shipped, verified behavior is the template system.

### 2.3 State model

`src/state/types.ts` defines one frozen, immutable `SystemState` snapshot,
rebuilt on every source event:

```
SystemState {
  activity: 'offline'|'escalation'|'building'|'reviewing'|'idle'  // priority order, highest first
  gatewayUp: boolean
  tasks: { building: TaskProgress[]; review; qa; ready; done: number }
  escalations: { open: number; topReason? }
  resources: { gpus: GpuResource[]; cpuPct; ramPct; gaming; contended }
  counts: { costUsd; tokRateHive; tokRateClaude; parseFailRate }
  tier: 'idle'|'busy'|'gaming'|'offline'   // render governor tier, §2.5
  ts: number
}
```

`TaskProgress` (one per in-progress task) carries `slug, title, turns,
progress (0..0.95 soft estimate = turns/expectedTurns), stalledMs, lastAction,
hiveTokens, claudeTokens, project` — `lastAction` is the live "now doing"
string surfaced on cards (§5).

- `src/state/derive.ts` — pure functions: activity priority derivation, GPU
  5060-class classification, resource contention, task-progress estimate,
  status counts, `buildSystemState()` (the single point that assembles a
  full snapshot from raw poll payloads). Fully unit-tested, no DOM.
- `src/state/sources.ts` — adapters that translate raw gateway/WS payloads
  into store updates; holds a small mutable cache so any single-source event
  (e.g. a scout poll) triggers a full re-derive + publish.
- `src/state/store.ts` — a pub/sub store (`subscribe/update/emit`). Activity
  transitions **dwell 1.5s** before committing (avoids UI flicker on a
  single noisy poll) **except** `escalation` and `offline`, which bypass the
  dwell and commit instantly — an outage or a new escalation must be
  reflected on the very next tick.

### 2.4 Data ingress: polling + WebSocket

Two orthogonal channels, both governed by the render tier (2.5):

- **Poll scheduler** (`src/scheduler.ts`) — named jobs at independent
  intervals (`board` 10s, `scout` 3s, `right`/escalations+calendar+docker+git
  30s, `suno` 60s by default), driven by `setTimeout` chains. Suspends
  entirely when the wallpaper host reports paused or `document.hidden`;
  resumes with an immediate re-fetch. The governor rewrites `board`/`scout`
  interval on every tier change (`scheduler.setInterval(name, ms)`).
- **WebSocket** (`src/ws.ts`) — two independent connections:
  - `ws://<gateway>/v1/events?token=<bearer>` — global event stream (task
    progress, moves, escalations, chat, alerts). Requires a bearer token;
    without one it retries every 10s rather than opening.
  - `ws://<gateway>/board/events` — board-only event stream, no token
    required.
  Both use **exponential backoff reconnect**: start 1s, ×2 per failed
  attempt, capped at 60s, reset to 1s on a successful `onopen`. Malformed
  JSON frames are silently dropped (`JSON.parse` in a try/catch). Both
  connections tear down on `visibilitychange → hidden` and reconnect on
  `visible` (if not explicitly suspended). `suspend()/resume()` are exposed
  so the pause integration (host-driven, or governor `tier==='gaming'`) can
  fully stop both sockets without tearing down poll state.
  Frame → UI shaping is pure (`src/ws_frames.ts`, unit-tested): known frame
  `type`s map to a ticker `{label, css}`; unknown types return `null` and are
  dropped, not rendered.

### 2.5 Resource governor / render tiers

`src/resource/governor.ts` is a small stateful hysteresis machine mapping
GPU/host telemetry → one of 4 tiers, each with its own `RenderBudget` (chart
FPS/point caps, graph node caps, animation on/off, poll intervals, WS mode
`all|board|none`):

| Tier | Trigger | WS mode | Poll cadence |
|---|---|---|---|
| `idle` | nothing else applies | `all` | scout 3s / board 10s |
| `busy` | a monitored GPU util>70% or temp>70°C | `all` | scout 5s / board 10s |
| `gaming` | a foreground-app/game flag is set, OR contention (util>85%, temp>78°C, free VRAM<1500MB) | `board` only | scout 15s / board 20s |
| `offline` | gateway unreachable | `none` | scout/board 30s |

Hysteresis: leaving `gaming` requires **all** metrics below the lower
thresholds (util<70, temp<65, free VRAM>2000MB) simultaneously — prevents
tier flapping at a boundary value. `offline` is instant in both directions
(no hysteresis — an outage or recovery reflects immediately). In `gaming`,
heavy renderers (charts, force graph, animation) drop to zero budget and the
WS is capped to the lightweight board channel only, so the app is cheap
enough to coexist with a foreground application/game.

### 2.6 Error boundaries + reconnect summary

- A panel's `relevance()`, `mount()`, or `update()` throwing is caught at
  the call site (layout applier / main loop) and logged; the panel renders
  an inline error state or is treated as hidden — **one panel's failure
  never blocks the layout pass or other panels' updates.**
- Every gateway fetch has an 8s timeout (`AbortController`) and every poll
  path catches its own error, logs a `console.warn`, and falls back to the
  last-known cached value or a null/empty result — no unhandled promise
  rejection can stop the scheduler.
- Board *mutations* (pause/resume/create task/decompose/etc.) use a
  short-lived session token fetched once from the gateway and cached; a
  401/403 response clears the cache and retries once with a freshly fetched
  token (handles the gateway regenerating its token on restart).
- WS reconnect: see §2.4 (exponential backoff, malformed-frame drop).
- On a full gateway restart the client self-heals with **no reload
  required**: the next poll tick and the next WS reconnect attempt both
  succeed once the gateway is back, and `SystemState.gatewayUp` (and thus
  `activity: 'offline'`) flips within one poll interval.

## 3. Panels

All panels are self-contained modules under `src/plugins/*.ts` (a few wrap
rendering helpers in `src/panels/*.ts`). Endpoints are relative to the
gateway base URL (dev: `/api` proxy; prod: direct, e.g. `http://127.0.0.1:8766`).

| id | title | data source | renders |
|---|---|---|---|
| `crew-board` | Crew Board (glance) | `GET /board/state`, `/board/stats`; board WS | hero card for the top in-progress task (progress ring, "now doing" line, turn count, token chips), READY/QA/REVIEW/DONE column counts, cost/token footer |
| `crew-board-full` | Crew Board — Full | embedded `<iframe>` to `GET /board?embed=1[&project=]`, self-fetching | the gateway's own full kanban page (columns, task detail, transcript, diff, mutations) framed inline; theme pushed in via `postMessage`; blanked to `about:blank` on suspend/gaming |
| `kpi` | Status (hero band) | derived from `SystemState` only | one synthesized pulse word (OFFLINE/GAMING/BUILDING/NEEDS YOU/IDLE) + vitals chips, one live "lane" row per building task (project, now-doing, turns, progress bar, stall flag), 4 headline numbers with sparklines (done, spend, ready, hive tok/s) |
| `telemetry` | Telemetry | derived `BoardStats` rolling buffer + `GET /board/tokens-by-day` | uPlot charts: cost/token rate history, tokens-per-day bars, smoke pass-rate, parse-fail rate |
| `gpu` | GPU / Host | `GET /v1/scout/status` (bridged via scout poll) | per-GPU gauge (util/temp/VRAM), a 4080-style "AI may use this GPU" auto/on/off mode switch (`GET/PUT /v1/gpu-mode`), sparkline history |
| `system` | System / Services | `GET /v1/scout/status` | service up/down dots + uptime, CPU/RAM/disk bars |
| `docker` | Docker | `GET /v1/docker/status` | container list (name, image, state, health); hidden entirely if Docker isn't installed |
| `activity-feed` | Activity | `logAction()` sink (in-process events) + `GET /v1/git/activity` + board/escalation bridges | unified chronological feed (hive actions tagged `hive`, commits tagged `git`) with pinned, clickable, approve-able "needs you" rows (open escalations, review-status tasks) at the top |
| `escalations` | Escalations | derived from `SystemState.escalations` (fed by `GET /v1/escalations`) | hero-sized alarm panel, instant (no dwell) when `open > 0` |
| `agenda` | Agenda | `GET /v1/calendar/jobs/upcoming?n=` | next N scheduled jobs |
| `terminal` | Terminal | `ws://<gateway>/v1/term` (bearer or session-token auth) | tabbed multi-session PowerShell-over-WebSocket PTY (xterm.js), up to 8 concurrent sessions, add/close tabs |
| `graph` | Knowledge Graph | `GET /v1/graph/god-nodes` (seed), `/v1/graph/neighbors` (on click), `/v1/graph/explain` (on hover) | d3-force node/edge canvas; node-count budget driven by render tier |
| `wiki-review` | Wiki Review | `GET /v1/wiki/reviews` + resolve/dismiss/research POSTs | open contradiction/gap review items with inline actions |
| `content-gallery` | Content Gallery | board tasks where `kind==='content'` (from the board poll) | prompt + generation state + result thumbnails for image/video requests |
| `projects` | Projects | self-fetches `GET /board/state`; mutations via evolve/suggest, evolve/go, enable/disable | per-project workload, hive on/off toggle, "Suggest next work" / "Go build it" |
| `weather` | Weather | Open-Meteo public geocoding + forecast APIs (keyless) | current conditions + short forecast for a configured location; multi-instance |
| `tokens-day` | Tokens / Day | `GET /board/tokens-by-day` | opt-in standalone chart (folded into Telemetry by default) |
| `clock` | Clock | none (local time) | clock/uptime demo panel |
| `manager-toggle` | Manager | none | toggles the Module Manager overlay |

Two panels are present in the tree but **not** wired into the live barrel
(kept as reference/back-compat): `needs-you` (folded into `activity-feed`)
and the standalone `actions-log`/`git-activity` pair (merged into
`activity-feed`).

### 3.1 Module Manager / multi-instance layer

`src/plugins/instances.ts` lets any plugin type be instantiated more than
once with independent settings (`{instanceId, type, settings, geometry}`,
persisted to `localStorage['dash:moduleInstances']`). Dormant by default —
with zero stored instances the single-instance-per-type path above is
unchanged. `src/plugins/module-manager.ts` provides the add/duplicate/remove
UI plus a schema-driven per-instance settings form generated from a plugin's
`settingsSchema`.

## 4. Theming

One `data-theme` attribute on `<html>` selects the whole palette; there is
no per-panel theming. The token model:

- A base `:root` block in `index.html` defines every token used anywhere in
  the app: `--bg`, `--bg2`, `--card`, `--line`, `--ink`/`--dim`/`--faint`
  (text hierarchy), `--copper`/`--amber`/`--amber-glow` (the single accent
  family — **one accent token drives the whole surface**: buttons, focus
  rings, active states, the brand glyph, chart strokes all reference it,
  never a hardcoded color), `--green`/`--red`/`--cyan` (status-only, used
  sparingly), plus ~15 semantic surface tokens (`--panel`, `--topbar-bg`,
  `--overlay-bg`, `--cell-bg-top/bot`, `--scrollbar-thumb`, `--esc-bg`,
  `--on-amber` for text-on-accent, etc.) so every custom widget (dropdowns,
  scrollbars, hover states, alarm badges) stays on-theme without a bespoke
  override per theme.
- Each theme is one `html[data-theme="<name>"] { --token: value; ... }`
  block overriding every token above, plus a matching `html[data-theme="<name>"]
  body { background: ...; }` block for a **signature background treatment**
  (radial glow, scanline overlay, perspective grid, flat solid — CSS-only,
  no heavy per-frame animation, so it stays cheap under a live-wallpaper
  render budget). All colors are OKLCH; chroma is kept modest so accents
  never read as neon, and status colors are spent only on live signals.
- Selection: an inline `<head>` script reads `localStorage['<app>.theme']`
  before first paint and sets `document.documentElement.dataset.theme`
  synchronously (no flash-of-wrong-theme). A topbar cycle button advances
  through the registered theme list, persists the choice, and dispatches a
  `<theme-change>` DOM event.
- Cross-surface sync: on every theme switch (and once on load) the client
  `PUT`s `{theme}` to the gateway at a loopback-only endpoint so other
  paired clients/hosts can pick up the same theme; the embedded full-board
  `<iframe>` is a **different origin** (it loads from the gateway, the shell
  loads from `file://`/the dev server), so `localStorage` doesn't cross —
  the shell instead `postMessage`s `{type:'theme', name}` into the iframe on
  load and on every switch, and the framed page listens for that message.
- Adding a theme = one new `[data-theme="x"]` CSS block overriding the full
  token set (copy an existing block, keep the same key list) + one body
  background block + registering the name in the cycle list. No JS/layout
  changes required — every panel and chart re-skins automatically because
  nothing reads a raw color, only tokens.

## 5. Live agent "thinking" surface

In-progress work exposes a live, human-readable "what is it doing right
now" line, sourced from `TaskProgress.lastAction` (populated from the
board API's `last_action` field, refreshed every board poll / board WS
event):

- **`crew-board` hero card** — a `.hero-now-doing` row directly under the
  task title, prefixed by a pulsing dot, showing the current action text
  (HTML-escaped). A circular progress ring (SVG `stroke-dashoffset`) around
  a `%` figure animates/pulses on every observed progress change.
- **`kpi` lane rows** — one row per concurrently-building task, each with
  its own now-doing line (`t.lastAction ?? 'working…'`), a turn counter, a
  fill-bar proportional to the soft progress estimate, and a stall flag
  (`stalledMs` past a threshold, no update recently) with a hazard-striped
  border treatment.
- **QA/review cards**: the same live text surfaces inside the embedded full
  board (`crew-board-full`, an iframe onto the gateway's own kanban page) —
  that page is server-rendered by the gateway, outside this dashboard's own
  DOM, and is out of scope for this spec beyond the embed contract (URL,
  theme `postMessage`, token isolation — the shell never forwards its own
  device bearer into the frame).
- **`activity-feed` needs-you rows** — review-status tasks and open
  escalations are pinned at the top of the activity feed as clickable,
  one-click-approvable rows (`REVIEW`/`ESC` tag + title + reason/slug), so a
  human sees exactly what is waiting on them without opening the full board.
- A rising-edge alert (`src/alerts.ts`) fires once per **new** escalation
  (not on every poll): flashes the topbar red, plays a two-tone synthesized
  WebAudio stinger (no audio asset), ducks any background audio, and raises
  a desktop `Notification`. Clears the moment `open_count` returns to 0.

## 6. Build & serve model

- **Dev**: `npm run dev` → Vite dev server on a fixed port, proxies
  `/api/*` to the gateway origin (rewriting the `/api` prefix) so the
  browser never has to deal with CORS in development.
- **Build**: `npm run build` → `tsc -b && vite build`. Output: `dist/`
  (`index.html` + `assets/*`), `vite.config.ts` sets `base: './'`
  (relative asset paths) — **required** for any serving context that isn't
  an HTTP root (a file:// load resolves an absolute `/assets/...` to the
  drive root and 404s). This also makes the build portable to any static
  file host, not just the Windows-specific wallpaper host.
- **Serve, cross-platform (no OS wallpaper host)**: `npm run serve` runs
  `vite preview --host --port <port>` against the built `dist/`, binding to
  all interfaces — the correct path for running this as a plain browser-tab
  dashboard on Linux/macOS/any headless box, or inside a container. Any
  static file server pointed at `dist/` also works, since the build has no
  server-side runtime dependency — it's a pure static SPA that talks to the
  gateway over HTTP/WS at runtime.
- **Windows live-wallpaper deploy** (optional, platform-specific, not
  required for the app itself) — the built `dist/` is copied into a
  wallpaper host's library folder and set via that host's CLI; the host
  must render with a Chromium engine that does not enforce CORS on a
  `file://` origin (a WebView2-class engine), or the runtime gateway calls
  fail. This is an OS/host integration detail, not part of the app's own
  contract — the app has zero import-time knowledge of any specific host,
  guarding all host hooks behind `typeof window !== 'undefined'` checks and
  optional global callbacks the host may or may not call.
- **Verification before any deploy** (`npm run rendergate`): builds, then
  runs a Playwright suite (`rendergate/`) that serves the prod build behind
  a deterministic mocked gateway (every HTTP fixture stubbed; WebSocket
  endpoints deliberately left unmocked to assert graceful degradation) and
  asserts, for 5 locked viewport targets (ultrawide, 3 landscape sizes,
  portrait): the correct template was chosen, a minimum panel count is
  present and in its designed slot, no panel overflows its grid cell, and
  the page itself never grows a scrollbar.

## 7. Verifiable checkpoints

1. **Board state reflects in real time.** With the gateway mock/live and a
   task moved from `ready`→`in_progress`, the `crew-board` hero and `kpi`
   lane both show the task within one `board` poll interval (≤10s default)
   or immediately on a `task_moved`/`task_progress` WS frame — no reload
   required. *Probe:* mutate the fixture/board, watch `#v2-hero-body` /
   `.hero-lane` DOM update without a page navigation.
2. **Offline → online reflects without reload.** Kill the gateway process;
   within one poll interval `SystemState.gatewayUp` flips false,
   `activity==='offline'` commits instantly (bypasses the 1.5s dwell), and
   the topbar live-dot/label switch to the offline state. Restart the
   gateway; the next poll tick and the next WS backoff attempt both
   reconnect and the UI returns to normal — no manual refresh. *Probe:*
   toggle the backing gateway process, watch `#live-dot`/`#live-label`.
3. **Render gate passes on every locked viewport.** `npm run rendergate`
   exits 0: for all 5 targets, the correct template is picked, core panels
   render in their designed slot, zero panels overflow, no page scrollbar.
   *Probe:* `npm run rendergate` (spawns the mock gateway + Playwright;
   non-zero exit or a written report = fail).
4. **Theme switch is instant, no-flash, and persists across a reload.**
   Clicking the theme cycle button re-skins every panel/chart on the next
   frame (no full re-render, no flash of the old theme on reload — the
   inline head script applies the stored theme before first paint).
   *Probe:* cycle the theme, reload the page, confirm
   `document.documentElement.dataset.theme` matches the pre-reload value
   with zero visible flash.
5. **A single panel failure does not take down the dashboard.** Force a
   panel's `mount()` or `update()` to throw (e.g. inject a bad settings
   value); the rest of the grid keeps updating on the normal cadence, the
   failing panel shows an inline error state (or is hidden) instead of a
   blank/frozen page, and the browser console shows exactly one logged
   error per failing call — no uncaught exception, no dead render loop.
   *Probe:* monkey-patch one plugin's `update` to throw, watch neighboring
   panels' data continue to tick.
