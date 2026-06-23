# Hive Companion App

Flutter mobile app for the Artificer gateway — voice/text chat, crew-board view,
and push notifications from the hive.

## Platform support

| Platform | Status |
|----------|--------|
| Android  | Supported (primary target) |
| iOS      | Supported (requires Xcode on macOS) |
| Windows desktop | Builds (experimental) |

## Prerequisites

- Flutter SDK 3.11+ (`flutter --version`)
- Android SDK / Xcode (for device builds)
- A running Artificer gateway (see [../docs/QUICKSTART.md](../docs/QUICKSTART.md))

## Build

```bash
# Debug build for connected device
flutter run

# Release APK (Android)
flutter build apk --release
# APK is at: build/app/outputs/flutter-apk/app-release.apk

# Install to connected Android device
flutter install
```

## Pairing (QR code flow)

1. Start the Artificer gateway (`python -m gateway` or `scripts/start-all.ps1`).
2. Open the admin UI at `http://127.0.0.1:8766/admin/` and generate a pairing code.
3. Launch the app on your phone — tap **Pair** and scan the QR code shown in admin.
4. The app now has a device Bearer token and connects over Tailscale or LAN.

## Gateway URL

The app talks to `http://<gateway-ip>:8766`. By default:
- Same machine: `http://127.0.0.1:8766`
- Phone over Tailscale: `http://<tailscale-ip>:8766` (set in app Settings)

## Architecture

```
app/lib/
  main.dart          # Entry point + MaterialApp
  screens/           # Chat screen, Crew board, Settings
  services/          # WebSocket client, REST client, auth token store
  models/            # Typed DTOs matching gateway API shapes
```
