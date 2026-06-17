# Long-conversation E2E scenarios

10 realistic, ≥30-turn conversations Penguin would actually have with the
stack. Each scenario lives in its own `<NN>_<slug>.jsonl` file so the
runner can pick them up individually or batch-run all ten.

## File format

One JSON object per line. Two object types:

```jsonc
// User turn — required fields
{"role": "user", "text": "Hey Terry, ..."}

// Optional pause / sleep — useful for time-spanning scenarios
{"role": "sleep", "seconds": 60}

// Optional metadata header (first line only)
{"role": "meta", "scenario": "tokyo-trip", "min_turns": 30,
 "device": "phone", "bot": "terry", "tags": ["personal-assistant", "vault"]}
```

The runner (to be built on top of `scripts/e2e_chat_driver.py`) ignores
unknown roles, so we can extend the schema later without breaking
existing scenarios.

## How they're scored

After each scenario runs:
1. **Transcript** is captured (chat_log table is the source of truth).
2. **Stumble audit** — did the assistant know what to do? Categories:
   missing skill, missing tool, missing prompt clause, tier-escalation
   bug, retrieval miss, fact-extraction miss.
3. **Skill-gap closure** — for each catalogued gap, design the smallest
   skill that fills it (under `~/.claude/skills/` or `Ai-Team-Vault/skills/`)
   and re-run the same scenario. Closure is measured by stumble-count
   reduction.

## Coverage matrix

| # | Slug | Headline | Tier surface | Cross-bot | Device | Time-spanning | Adversarial |
|---|------|----------|--------------|-----------|--------|---------------|-------------|
| 1 | tokyo-trip-planning | Personal assistant | planner+researcher+vault | — | desktop | yes (calendar fire mid-thread) | mid-thread budget pivot |
| 2 | recipe-app-build | "Make me an app" | orchestrator tier escalation | claude-code handoff | desktop | — | — |
| 3 | journaling-60d | Sentiment trend | librarian + chat_recall over 60d | — | phone | yes (60 days simulated) | — |
| 4 | image-aesthetic-iter | Iterative image gen | image pipeline + user_facts | — | phone | — | contradiction with prior turn |
| 5 | hive-feature-review | Code review | researcher → claude-code escalation | maggy → claude-code | desktop | — | knowledge-gap escalation |
| 6 | cancer-research-thread | Long research thread | librarian + chat_recall + vault learn | — | phone-then-desktop | yes (week-by-week) | ambiguity, citation chase |
| 7 | what-should-i-do-tonight | Decision-fatigue | calendar + weather + preferences | — | phone | yes (real-time calendar pull) | — |
| 8 | partner-birthday | Family coord | vault people + calendar + image | terry → claude-code | desktop | yes (multi-day) | multi-stakeholder mention |
| 9 | novel-chapter-feedback | Long-form writing | chat_recall over 8 chapters | — | desktop | — | character-name consistency check |
| 10 | computer-debug-voice | Voice diagnostic | helper escalation + tool selection | — | phone (voice) | — | mid-conversation pivot |

## Running

Once the runner is built (separate task), invoke as:

```
python scripts/run_scenarios.py --scenario 01 --token <t> --host http://127.0.0.1:8766
python scripts/run_scenarios.py --all --out runs/
```

Each run produces `runs/<scenario>/<timestamp>.{transcript.json,events.jsonl,audit.json}`.

## Authoring rules

- **≥30 turns each.** Fewer turns won't exercise the long-conversation
  surfaces (mid_summary refresh, long_digest compression, chat_recall).
- **Real-user shape, not contrived.** Bias toward open-ended asks that
  drift across multiple capability surfaces.
- **At least one adversarial element per scenario** — ambiguity,
  pivot, contradiction, or "forget that" — so the audit can spot
  graceful-degradation gaps.
- **No hard-coded vault state.** Scenarios that rely on prior history
  must seed it via early turns ("remember that I prefer X") rather than
  assuming it's already written.
