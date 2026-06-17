# hive-dashboard — working rules

Hive Command Center v3 wallpaper (vanilla TS + Vite + uPlot + xterm.js),
deployed as a Lively WebView2 wallpaper at `hivecmd.v01`. Warm-black + amber
OKLCH. See `docs/REDESIGN.md` for the AAA redesign plan (Karpathy method).

## Verify before deploy (non-negotiable)

Every visual / layout change MUST pass the render gate before it ships to the
wallpaper:

```bash
npm run rendergate   # builds, then Playwright-asserts all 5 targets
```

The gate loads the prod build against a deterministic gateway mock
(`rendergate/`) and asserts, for 5440×1440 / 1920×1080 / 2560×1440 /
3840×2160 / portrait 1440×2560: correct template, core panels in their slots,
zero panel overflow, no wallpaper scrollbar. A red gate blocks deploy.

Also green before any commit: `npm run build` (tsc + vite) and `npm test`
(vitest).

## Layout system

- Hand-designed `grid-template-areas` per screen-class in `src/layout/templates.ts`
  (`ultrawide` / `wide` / `portrait`), chosen by `pickTemplate(w,h)`. NO
  auto-packer — panels sit in fixed, named slots. A panel absent from a
  template's `slots` is intentionally not shown there.
- Add a panel to a screen-class = give it an area in that template's `areas`
  grid + a `slots[panelId]` entry. If you add a panel id, also add it to the
  rendergate's expected coverage (its fixture data must make it relevant).

## Deploy (after the gate is green)

Build → copy `dist/index.html` + `dist/assets/*` into the Lively dir →
`Lively.exe setwp --file <…\hivecmd.v01\index.html>` (point at index.html, not
the dir) → confirm a NEW `Lively.Player.WebView2` PID. Do NOT `Remove-Item`
the Lively assets dir (protected path) — overwrite in place. `vite.config.ts`
must keep `base: './'` (absolute `/assets` 404s under file://).

## Scope

Warm-black + amber stays. No framework swap. Keep files focused.
