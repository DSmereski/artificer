---
name: plan-vault
description: Use when a plan is finalized (ExitPlanMode), re-planned, or the user says "save the plan to the vault" / "update the plan". Archives plan files to the Ai-Team vault so they persist and can be looked back at, and keeps them updated in place.
---

# plan-vault

Durable archive for plans. Every plan made (karpathy / plan mode) gets saved to
the Ai-Team vault (`~/Ai-Team-Vault/plans/`) so it survives the chat and can be
reviewed later. Re-running on the same project UPDATES the same file (no
duplicates); the vault is git-tracked so history is kept.

This is the karpathy **Environment** layer for planning: the plan stops being a
throwaway chat artifact and becomes a navigable, updatable part of the knowledge
base. Pairs with `[[karpathy]]` (which makes the plan) and the planning rule.

## When to run

- A plan was just approved (ExitPlanMode) — save it.
- An existing plan changed materially (scope shift, steps done, decisions
  revised) — re-save to update it in place.
- User asks to save / update / look back at a plan.

## How

Run the save script. It upserts one file per project (stable slug) + refreshes
`INDEX.md`:

```bash
python "~/.claude/skills/plan-vault/scripts/save_plan.py" \
  --plan "<path to the plan .md>" \
  --project "<project name>" \
  --title "<one-line description>"
```

- `--plan` — the plan file. Plan-mode plans live at
  `~/.claude/plans/<slug>.md`. Any .md plan works.
- `--project` — groups the plan. Same project → same vault file → UPDATE in
  place. Pick the real project name (e.g. `MyProject`, `MyApp`).
- `--title` — index line summary. Optional; defaults to the plan's first
  heading.

Output prints the saved vault path + index path. The vault file gets a
`<!-- project | updated | source -->` header prepended; the body is the plan
verbatim.

## Updating

To update, just run the script again with the same `--project`. It overwrites
that project's vault file with the latest plan and bumps the date. To capture
history snapshots, commit the vault between updates (`git -C ~/Ai-Team-Vault`).

## Self-improvement

Hit friction (wrong naming, need history snapshots, multi-plan-per-project,
etc.)? Update this SKILL.md + the script, then sync:
`python ./hive/scripts/sync_skills.py`
