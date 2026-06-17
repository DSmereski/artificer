# Hive Command Center — DESIGN

Derived from the live tokens in `index.html` and the established build.

## Theme

Dark, warm. Scene: an operator glancing at the dim edges of a 49" ultrawide
at night while the foreground holds a code editor. The surface must recede so
the foreground app stays primary, yet stay legible in peripheral vision. That
forces near-black, not mid-gray, and warm (amber-tinted) not cold.

## Color

OKLCH throughout. Strategy: **restrained surface, committed accent** — a
warm near-black canvas carries ~90%, amber/copper is the single identity
accent, status hues appear only on live signals.

| Role | Token | Value |
|---|---|---|
| Canvas | `--bg` | `oklch(0.14 0.014 55)` warm near-black (never `#000`) |
| Raised | `--bg2` / `--card` | `0.17` / `0.20` L, same warm hue |
| Hairline | `--line` | `oklch(0.30 0.02 64)` |
| Ink | `--ink` / `--dim` / `--faint` | `0.95` / `0.74` / `0.56` L, warm-tinted |
| Accent | `--amber` / `--copper` | `oklch(0.83 0.15 78)` / `0.74 0.13 56` |
| Glow | `--amber-glow` | `0.85 0.17 80` (the honeycomb mark + active pulses) |
| Status | `--green` `--red` `--cyan` | up / trouble / info — live signals only |

Chroma stays modest (≤0.17) so nothing reads as neon. Status colors are
spent sparingly — a dashboard where everything is colored says nothing.

## Typography

- `--font-ui` Inter for labels (short CAPS, letter-spaced, `--faint`/`--dim`).
- `--font-mono` JetBrains Mono for all numbers + data (alignment, ops feel).
- Hierarchy: hero KPI numbers are huge; panel labels are small caps; body
  data is mono at a steady small size. Weight + scale carry hierarchy, not
  many sizes.

## Layout

- Fixed 5120×1440 canvas. 56px top bar + a KPI hero band + a CSS-Grid panel
  field (12 columns, band rows sized to the band count — see
  `layout/apply.ts`). Phone embed is a separate narrow variant.
- The grid is **stable**: it re-lays-out only when the visible panel *set*
  changes, never on a data tick (`main.ts` signature gate). Panels clip +
  scroll their own content; no panel may balloon its row.
- Gutters: 8px. Panels are bordered regions (`--line`), not floating cards;
  this is one continuous console, not a tile soup.

## Components

- **Panel**: `.panel-header` (small-caps label + optional right-aligned
  status chip) over a content region that owns its own overflow.
- **KPI hero**: oversized mono numbers with a tiny caps label + sparkline.
- **Status chip / dot**: tiny, status-hued, used only when a panel has live
  state worth flagging.

## Motion

Pulses signal real change (build start, alert). Ease-out only. Never animate
layout/position — the grid holds still by contract.

## Idle / empty states

Idle is a first-class state (the board is often paused or quiet). An empty
panel shows a calm one-line resting message in `--faint` ("Quiet. The swarm
has it."), never a blank void or a broken-looking gap.
