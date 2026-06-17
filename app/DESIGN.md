# Design

Visual system for the AI-Team / Hive app. Direction: **Alive + Kinetic** —
a living-hive cockpit on a warm-black base, copper/amber accents, motion that
reflects real swarm activity. Dark-only. Implemented as Flutter `HiveTokens`
(ThemeExtension + statics) in `lib/theme/`.

## Color

OKLCH, warm-tinted neutrals (every neutral leans toward the copper hue,
chroma ~0.008–0.018 — never pure `#000`/`#fff`). Strategy: **Committed** —
copper/amber carry the identity; green = healthy/live, red = error/escalation.

| Role | OKLCH | Use |
|---|---|---|
| bg | `0.14 0.014 55` | app base (warm near-black) |
| bg2 | `0.17 0.016 58` | inputs, recessed |
| card | `0.20 0.018 60` | task cards, message bubbles |
| line | `0.30 0.02 64` | borders, hairlines |
| ink | `0.95 0.012 72` | primary text |
| dim | `0.74 0.014 72` | secondary text |
| faint | `0.56 0.014 68` | meta, timestamps |
| copper | `0.74 0.13 56` | identity accent, building state |
| amber | `0.83 0.15 78` | primary accent, active nav, CTAs |
| amberGlow | `0.85 0.17 80` | glow/sweep highlight |
| green | `0.80 0.16 150` | live, shipped, healthy |
| red | `0.66 0.17 25` | error, escalation, offline-danger |
| cyan | `0.80 0.10 200` | reserved: system/telemetry data only |

App background is a subtle radial: `radial-gradient(120% 80% at 50% -10%,
oklch(0.17 0.03 60), oklch(0.10 0.01 55))` — gives cinematic depth without
flatness.

## Typography

- Family: Inter (UI) + a mono with tabular figures (JetBrains Mono / SF Mono)
  for telemetry numerals, slugs, timestamps.
- Scale (≥1.25 contrast between steps), weights lean heavy for headers:
  - Display/title 21–22 / **800** / -0.01em
  - Section header (klabel) 11 / 700 / **+0.16em uppercase** / amber, with a
    trailing hairline gradient rule
  - Body 13–14 / 600–700
  - Meta 10–11.5 / 500 / faint
- Numerals (counts, tokens, cost, time): **mono + tabular**.

## Motion (the "alive" layer)

Ease-out only (ease-out-quart/expo); no bounce/elastic. Tokens:
`fast 120ms · base 200ms · emphasized 300ms`. Living loops:

- **pulse** — status/liveness dots + active-worker markers: opacity 0.5→1 +
  scale 0.9→1.15, ~1.6s infinite ease-in-out.
- **glow** — "now building" card + voice mic: box-shadow 0 → amber blur, ~2.6s.
- **sweep** — a 40%-wide amber highlight translating across the now-building
  card, ~2.8s, signals active work.
- Nav active item: amber glow halo on the icon tile.

**Every loop is gated on real state + reduced-motion.** Nothing pulses when
nothing is happening; all loops stop under `MediaQuery.disableAnimations`.

## Components

- **Now-building card** (home + board in-progress): copper→amber gradient
  border, glow loop, scan-sweep, progress bar (copper→amberGlow), turn/token
  readout. The signature "alive" element.
- **Status chip**: pulsing dot + label (green live / amber syncing / grey
  offline). Color + word, never color alone.
- **Hex logo mark** (⬡) with amber drop-shadow — brand anchor in headers.
- **Section header**: uppercase amber klabel + count, hairline-gradient rule.
- **Task card**: warm card, slug (mono) + title (700) + state pill
  (building=copper, review=amber) + assignee with pulsing presence dot.
- **Chat**: bot bubble (card + hairline, bottom-left clipped), me bubble
  (copper→amber gradient, soft shadow, bottom-right clipped), **helper-trace
  line** (amber, pulsing dot — "routing / synthesizing").
- **Bottom nav / rail**: 5 phone / 11 desktop; active = amber + glow tile.
- **Telemetry readout grid** (board stats): hairline-gridded cells, mono
  values, tiny uppercase labels — borrowed density for the data views only.
- **Empty/quiet states**: amber bolt/hex + one calm line ("Quiet. The swarm
  has it.") — never a dead spinner.

## Layout

4px grid (`HiveTokens.s1..s8`), radius `rSm 6 / rMd 10 / rLg 16 / rPill 999`.
Vary spacing for rhythm — living surfaces get more breathing room, data views
go denser. Cards only where a card is the right affordance (tasks, messages);
home feed and stats are list/grid, not card soup.

## Bans (inherited)

No side-stripe borders, no gradient text, no decorative glassmorphism, no
hero-metric template, no identical card grids, no em dashes in copy.
