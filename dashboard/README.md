# Hive Command Center — Wallpaper Dashboard

Live interactive desktop wallpaper for the Hive gateway. 5120x1440 (super-ultrawide).
Rendered by Lively Wallpaper using WebView2 or CefSharp.

## Phase A Status

Build and TypeScript: CLEAN.
Lively: installed (winget) and wallpaper loaded via CLI. Rendering confirmed (CefSharp).
Gateway CORS: needs WebView2 to resolve (see below).

## Quick start (dev)

```powershell
cd C:\Projects\hive-dashboard
npm install
npm run dev
# open http://localhost:5175 in browser to preview
```

## Build + deploy to Lively

```powershell
npm run build
# Copy dist\ into Lively library:
xcopy /E /I /Y dist "C:\Users\<you>\AppData\Local\Lively Wallpaper\Library\wallpapers\hivecmd.v01"
# Set as wallpaper (Lively must be running):
& "C:\Program Files\Lively Wallpaper\Lively.exe" setwp --file "C:\Users\<you>\AppData\Local\Lively Wallpaper\Library\wallpapers\hivecmd.v01\index.html"
```

## Critical: Switch Lively to WebView2 (required for live data)

The wallpaper renders in CefSharp by default, which enforces CORS and blocks
`localfolder://` → `http://127.0.0.1:8766` fetches.

**Fix (one-time, the operator must do manually):**
1. Open Lively Wallpaper
2. Settings (gear icon) > Performance > Web Browser > select **WebView2**
3. Restart Lively
4. Re-set the wallpaper: run the setwp command above

After switching to WebView2, live board data will populate automatically.

## DAVID: Verify These 3 Things (Phase A gate)

### Check 1 — Renders (wallpaper visible full-bleed on the 5120x1440 panel)

1. Open Lively (it may already be running in the system tray).
2. You should see "Hive Command Center" as the desktop wallpaper on your ultrawide.
   - Topbar: `⬡ HIVE COMMAND CENTER` + clock
   - Left column: Crew Board section headers
   - Center: Live Stats (currently showing "Quiet. The swarm is offline." because CORS)
   - Right: Input Probe with a big amber button
3. If not visible: run the setwp command from the build section above.

### Check 2 — Input forwarding (a click registers)

1. With the wallpaper active (no window in front), click the amber button in the
   right column ("Click to Prove Input Forwarding").
2. The counter above the button should increment (0 → 1 → 2 ...).
3. The counter persists in localStorage — if you refresh/reboot, it starts from the
   last stored value (proving localStorage works too).

NOTE: Mouse forwarding is ON by default in Lively (InputForward=1 confirmed in settings).
If clicking does nothing: Lively Settings > Wallpaper > Input > ensure "Wallpaper input" is enabled.

### Check 3 — Game pause (Lively pauses wallpaper under fullscreen game)

1. Open any fullscreen game on the ultrawide.
2. The wallpaper should pause (Lively's built-in fullscreen detection).
3. When the game exits/minimises, the wallpaper resumes.
   - The `livelyWallpaperPlaybackChanged` hook is wired — when Lively calls it
     with `IsPaused:true`, a "PAUSED" badge appears in the topbar (visible before
     going fullscreen, verifiable in Lively's screenshot tool).
   - After switching to WebView2, run:
     `& "C:\Program Files\Lively Wallpaper\Lively.exe" screenshot --file C:\tmp\shot.jpg`
     to capture a headless screenshot for before/after comparison.

AppFullscreenPause confirmed as 0 (= pause on fullscreen) in Lively Settings.json.

## Live data (after WebView2 switch)

Gateway at `http://127.0.0.1:8766` — same host as wallpaper, loopback.
- `/board/stats` and `/board/state` are open (no token required)
- `/v1/*` endpoints need a device Bearer — enter via Lively right-click > Customise > Gateway Device Bearer Token

## File structure

```
src/
  main.ts          # Entry: clock, poll loop, board rendering
  gateway.ts       # Typed HTTP client (ported from ***REMOVED*** pattern)
  pause.ts         # livelyWallpaperPlaybackChanged hook + document.hidden
  props.ts         # livelyPropertyListener hook (token, pollInterval, motion)
  probe_input.ts   # Phase A click counter
public/
  LivelyInfo.json       # Lively wallpaper manifest (Type=1/Web, Arguments)
  LivelyProperties.json # Lively Customise panel (deviceToken, pollInterval, motion)
```

## Lively CLI reference

```powershell
# Set wallpaper (Lively must be running, path must be in Library)
& "C:\Program Files\Lively Wallpaper\Lively.exe" setwp --file "<library-path>\index.html"

# Take a screenshot
& "C:\Program Files\Lively Wallpaper\Lively.exe" screenshot --file "C:\tmp\shot.jpg" --monitor 0

# Pause/resume
& "C:\Program Files\Lively Wallpaper\Lively.exe" --play false
& "C:\Program Files\Lively Wallpaper\Lively.exe" --play true
```

## Divergences from plan (Phase A)

1. **Monitor is 5120x1440, not 5440x1440** — the plan spec'd 5440 but the actual
   display is 5120 wide. CSS uses 100vw/100vh so it adapts automatically. Design width
   viewport meta set to 5440 (plan spec) but rendered at 5120 — no visible impact.
2. **Lively uses CefSharp, not WebView2** — plan assumed WebView2 (zero-install, no CORS).
   CefSharp enforces CORS for localfolder:// origins. Fix: the operator switches to WebView2 in
   Lively Settings. setwp CLI confirmed to work once wallpaper is in the Library folder.
3. **setwp CLI won't import web files directly** — Lively 2.1.0.8 CLI logs
   "Unsupported command import file:web" for new web wallpapers. Workaround: copy dist/
   into Lively Library manually (xcopy command above), then run setwp.
4. **Already installed** — Lively 2.1.0.8 was already installed on the machine when
   winget ran; it reinstalled cleanly. The setwp command loaded the wallpaper successfully.
