# Skills

Hive ships its **own** Claude Code skills under [`skills/`](skills/). The author
also runs a set of excellent **third-party** skills/plugins that pair well with
Hive — those are **not bundled** (they keep their own licenses); install them
yourself from the sources below.

## Shipped with Hive (`skills/`)

| Skill | What it does |
|---|---|
| `karpathy` | Plan with the spec → verifier → environment method |
| `prompt-injection-defense` | Safely ingest untrusted external content |
| `competitive-feature-analysis` | Feature-gap research → build plan |
| `graphify` | Turn any input into a queryable knowledge graph |
| `plan-vault` | Archive + update plans in a vault |
| `vault-remember` / `vault-search` | Persist + recall notes across sessions |
| `delegate-to-hive` | Decide build-it-yourself vs hand to the crew board |
| `hive-test-suite` / `hive-restart-gateway` / `hive-e2e-chat` | Hive ops helpers |
| `dashboard-panel` | Scaffold a new dashboard panel |
| `android-emulation` | Headless Android test via adb |

## Companion skills the author runs (install these yourself)

Point your Claude Code at these — not affiliated with Hive, not bundled:

| Skill / plugin | Source |
|---|---|
| superpowers (brainstorming, writing-plans, TDD, debugging) | [claude-plugins-official](https://github.com/anthropics/claude-code) marketplace |
| impeccable (frontend design) | [impeccable](https://github.com/anthropics/claude-code) plugin marketplace |
| caveman (token-compressed mode) | Claude Code plugin marketplace |
| everything-claude-code (reviewers, TDD, planners) | [everything-claude-code](https://github.com/) plugin |
| everything-evenhub (Even G2 glasses) | EvenHub plugin |
| claude-flow (agentdb, sparc, swarm, v3, reasoningbank, hooks) | [github.com/ruvnet/claude-flow](https://github.com/ruvnet/claude-flow) |
| context7 (live library docs) | [github.com/upstash/context7](https://github.com/upstash/context7) |
| playwright (browser automation) | [github.com/microsoft/playwright](https://github.com/microsoft/playwright) |

Hive's own skills are MIT (see [LICENSE](LICENSE)). The companion skills keep
their own licenses — check each source.
