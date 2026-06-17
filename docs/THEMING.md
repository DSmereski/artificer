# Theming

The dashboard ships multiple themes; pick one at install (the `config/.theme`
file) or switch it in the dashboard settings.

## Shipped themes

- **warm-black** (default) — warm-black + amber, low-glare for 24/7 wall display.
- **light** — bright, high-contrast for daytime desks.
- **neutral-dark** — desaturated dark for mixed environments.

## How themes work

Theme tokens are CSS custom properties (`--bg`, `--accent`, `--line`, …). A theme
is a set of those values; switching swaps the token block without changing
layout. Chart colors read from the same tokens so a theme re-skins the whole
surface — panels, charts, and the crew board.

## Add your own

1. Copy an existing theme token block.
2. Change the OKLCH values (keep chroma low near the lightness extremes).
3. Register it in the theme picker.
4. Run the render gate to confirm no layout regression at every screen size:

```bash
cd dashboard && npm run rendergate
```

> Tip: keep one accent color and tint neutrals toward it — avoid pure `#000`/`#fff`.
