---
name: android-emulation
description: Use when you need to run/test an Android (Flutter or native) app on this machine without a physical device — spin up an emulator, install an APK, and drive + observe the UI entirely via adb (screencap + input). Covers the AVD lifecycle and the non-obvious gotchas (cleartext http, host-loopback, prefs injection, coordinate scaling).
---

# Android Emulation (headless drive via adb)

Run and verify an Android app on this machine with no physical device, driving
the UI through `adb` and *seeing* the screen via `adb exec-out screencap`.
This lets an agent do full UI verification (install → pair → interact →
screenshot) unattended.

Self-improvement: when you hit a NEW gotcha or a step here is wrong, update
this file, then run `python ./hive\scripts\sync_skills.py` so the
vault copy stays in sync.

## Paths (this machine)
- SDK: `%LOCALAPPDATA%\Android\Sdk`
- adb: `…\Sdk\platform-tools\adb.exe`
- emulator: `…\Sdk\emulator\emulator.exe`
- avdmanager/sdkmanager: `…\Sdk\cmdline-tools\latest\bin\`
- Add to PATH in Git Bash: `export PATH="$PATH:/c/Users/<you>/AppData/Local/Android/Sdk/platform-tools:/c/Users/<you>/AppData/Local/Android/Sdk/emulator"`

## Lifecycle

1. **System image** (one-time, ~1GB): `yes | sdkmanager.bat "system-images;android-35;google_apis;x86_64"`
2. **Create AVD**: `echo no | avdmanager.bat create avd -n p0test -k "system-images;android-35;google_apis;x86_64" -d pixel_6`
3. **Boot (background)**: `emulator -avd p0test -no-snapshot -no-boot-anim -gpu swiftshader_indirect` (run_in_background; it stays running)
4. **Wait for boot**: loop on `adb -s emulator-5554 shell getprop sys.boot_completed` == `1`.
5. **Install**: `adb -s emulator-5554 install -r path/to/app-debug.apk`
6. **Launch**: `adb -s emulator-5554 shell am start -n <pkg>/.MainActivity`

If a physical device is also attached, **always pass `-s emulator-5554`** —
bare adb errors with "more than one device/emulator".

## TOKEN ECONOMY (read this first)

Reading screenshots is the most expensive thing an agent does here — each
full-res image costs ~1.5k+ tokens AND stays in context, re-billed every
turn. Verify with TEXT first; screenshot last.

1. **UI tree, not pixels**: `adb -s emulator-5554 shell uiautomator dump /sdcard/ui.xml && adb -s emulator-5554 shell cat /sdcard/ui.xml` → grep for text/content-desc.
   **Flutter unlock**: Flutter only emits AccessibilityNodeInfo when an
   accessibility service is running — without it the tree is one opaque
   FlutterView. Force it once per emulator (headless, sound irrelevant):
   ```
   adb -s emulator-5554 shell settings put secure enabled_accessibility_services com.google.android.marvin.talkback/com.google.android.marvin.talkback.TalkBackService
   adb -s emulator-5554 shell settings put secure accessibility_enabled 1
   ```
   Then the dump shows text/content-desc/checked/enabled for every Flutter
   Semantics node (standard Text/Button/TextField emit automatically;
   custom-painted widgets need explicit `Semantics(label:)`).
   Researched alternatives (2026-06): appium-mcp / mobile-next mobile-mcp
   need the SAME TalkBack prerequisite plus Node+Appium sidecars (3 crashable
   processes) — only worth it for MCP-driven taps, not read-back. Maestro
   needs WSL2 adb bridge on Windows (brittle). Patrol = 30-90s build cycle
   per check (CI gate, not agent loop). Plain adb wins here.
2. **Assertions in code**: widget/integration tests (`flutter test`) — zero images.
3. **Logs**: temporary `print('[TAG] …')` + `adb logcat -d | grep TAG` for state.
4. Screenshot ONLY at milestones; then **crop the region of interest** +
   downscale + JPEG before Reading:
   `python -c "from PIL import Image; im=Image.open('s.png').crop((0,0,1080,260)); im.thumbnail((800,800)); im.save('s.jpg',quality=70)"`
5. If a human needs to see it, SEND the file to them instead of Reading it
   yourself — their eyeball is free; yours is metered.

## Observe + drive (no MCP needed)
- **Screenshot**: `adb -s emulator-5554 exec-out screencap -p > /c/tmp/shot.png` then Read the PNG.
  **ALWAYS downscale before Reading** — full-res phone shots (1080×2400) exceed
  the API's 2000px many-image limit once a conversation holds several images,
  poisoning the WHOLE conversation with repeating "image could not be
  processed" errors (only /compact clears it). Downscale first:
  `python -c "from PIL import Image; im=Image.open('shot.png'); im.thumbnail((900,1950)); im.save('shot_s.png')"`
  and Read `shot_s.png`.
- **Tap**: `adb -s … shell input tap X Y` — coords are **device pixels** (e.g. 1080×2400), NOT the scaled-down image you view. If the screenshot note says "displayed at 900×2000, multiply by 1.2", multiply your read-off coords by 1.2 before tapping.
- **Type**: `adb -s … shell input text "literal"`. **Unreliable** for long/special strings — it silently truncates and can double-insert. Prefer injecting state directly (see prefs trick).
- **Keys**: `keyevent 4`=BACK (also closes the soft keyboard cleanly), `3`=HOME, `66`=ENTER, `67`=DEL, `123`=MOVE_END, `111`=ESC.
- **Clear a field**: tap it → `keyevent 123` (end) → many `keyevent 67` (delete). Then **close the keyboard with `keyevent 4` BEFORE tapping buttons** — the soft keyboard shifts the layout, so post-typing taps land on the wrong widget.
- **Grant a runtime permission**: `adb -s … shell pm grant <pkg> android.permission.CAMERA`
- **Lifecycle (trigger onResume)**: `keyevent 3` (home) then `am start …` again — useful for poll-on-resume logic.

## Gotchas that cost real time

- **Cleartext HTTP is blocked.** Android 9+ refuses `http://` by default →
  every request fails silently (app looks "offline"). Add
  `android:usesCleartextTraffic="true"` to `<application>` in the manifest.
- **Reaching a host server from the emulator.** The emulator is NAT'd. Use
  `10.0.2.2` for the host loopback, OR (more reliable on Windows)
  `adb -s emulator-5554 reverse tcp:PORT tcp:PORT` and point the app at
  `127.0.0.1:PORT`. `adb reverse` is dropped when the adb server restarts —
  re-add it. Removing the reverse simulates the server going unreachable
  (clean way to test offline behavior without killing the real server).
- **A Tailscale-only / 127.0.0.1-only bind** on the host is reachable from the
  emulator via `10.0.2.2`/reverse, but NOT via the host's Tailscale IP (the
  emulator isn't on the tailnet).
- **Inject app state instead of typing it.** For debug builds, write
  SharedPreferences directly to avoid `input text` mangling:
  `adb -s … shell "run-as <pkg> cat /data/data/<pkg>/shared_prefs/FlutterSharedPreferences.xml"`
  to read; to write, **base64-encode the XML on the host and decode on device**
  (shell quoting otherwise strips the XML attribute quotes, and Git-Bash MSYS
  rewrites `/data/...` push paths):
  `B64=$(python -c "import base64;print(base64.b64encode(open('f.xml','rb').read()).decode())"); adb -s … shell "run-as <pkg> sh -c 'echo $B64 | base64 -d > /data/data/<pkg>/shared_prefs/FlutterSharedPreferences.xml'"`
  Flutter prefs keys are prefixed `flutter.` and typed (`<string name="flutter.key">…</string>`).
- **Debugging silent failures.** If the app swallows errors (e.g. a sync loop
  with `catch (_)`), add a temporary `print('[TAG] …: $e')`, rebuild, and read
  `adb -s … logcat -d | grep TAG`. Flutter `print` shows under the `flutter`
  logcat tag. Revert the print after.
- **Camera/QR scanning** won't work — the emulator camera is a synthetic 3D
  scene, so it can't read a real QR. Use a manual/token path instead.
- **Reinstall (`install -r`) may clear app data** (resetting pairing/session)
  even though it nominally keeps it — re-pair after reinstalling.
- Debug builds are slow to first frame — expect a Flutter splash for several
  seconds; re-screenshot after `sleep 7`.

## When to use a physical device instead
Use a USB device (`adb devices` shows its serial) when you need real BLE,
camera, GPS, or true network conditions. Enable USB debugging + accept the
host key. Everything else (UI flows, offline behavior, pairing via injected
token) is faster and fully scriptable on the emulator.
