---
name: delegate-to-hive
description: Use BEFORE taking on a substantive implementation task on any of the operator's projects — pause and ask whether it should become a crew-board item for the Hive to build autonomously instead of Claude doing it directly. Triggers when the operator asks to build/add/fix/implement a feature on a project that has a Hive crew-board project (ai-team, example-app, example-store, example-project, example-game, example-rts, example-hud, media-*, etc.), or when work is well-specified + parallelizable + not urgent. Not for conversational answers, quick edits, or interactive/exploratory work.
---

# Delegate to Hive (board item vs. do it myself)

the operator runs an autonomous build pipeline (the Hive crew board). Many tasks he
hands me could instead be **dropped on the board for the Hive to build** —
freeing me, running in parallel/background, and dogfooding the pipeline. Before
grabbing a substantive project task myself, **decide and ASK**.

## When this applies

A task is a **delegation candidate** when ALL of:
- It's real implementation work (a feature/fix/refactor) on a project that has a
  crew-board project (check the board's project list).
- It's reasonably **well-specified** or can be specced into clear acceptance
  criteria (the Hive needs a tight spec — see the `karpathy` skill).
- It's **not urgent / not interactive** — the operator doesn't need it this minute and
  doesn't need to watch it happen.

Skip (just do it myself, no ask) when:
- Conversational / a question / research / planning.
- A quick 1-2 file edit, a config tweak, or something faster to do than to spec.
- Urgent, interactive, or exploratory ("let's figure out X together").
- It touches the gateway/pipeline itself or infra the Hive can't safely build.

## The ask

When a task is a delegation candidate, before starting, ask the operator plainly:

> "Want me to add this to the Hive board for the crew to build, or handle it
> myself now? Board = autonomous/parallel/background; me = now/interactive."

Give a one-line recommendation:
- **Lean board** when: well-specced, parallelizable, background-friendly, a good
  dogfood, or you're already busy with other work.
- **Lean me** when: urgent, needs back-and-forth, small, or the Hive has
  repeatedly struggled with this kind of task.

If the operator already said which way he wants it, don't re-ask — respect it.

## How to add a board item (if delegating)

`POST /v1/... ` no — the board API is on the gateway:
1. Confirm the right project_slug exists + is **enabled** (GET `/board/state`
   projects; the Hive only runs enabled projects with a valid git path). Pick the
   real project (e.g. the Flutter app is `example-app`, not the disabled
   `example-app`).
2. Create the task (Bearer device token or X-Board-Token):
   `POST /board/tasks` with `{title, project_slug, body, acceptance_criteria:[{text,checked:false}...], files_of_interest, depends_on, tags}`.
   Write a tight spec + measurable criteria (apply the `karpathy` method).
3. To make the Hive actually work it: `POST /board/tasks/{slug}/assign {"assignee":"hive"}`
   then `POST /board/tasks/{slug}/move {"status":"ready"}`. The dispatcher picks
   up ready + assigned tasks (one per assignee at a time). Leave it `none`/backlog
   if the operator just wants it queued for later triage.
4. For a big/fuzzy goal, prefer `POST /board/decompose {"goal","project_slug"}` —
   the planner breaks it into a dependency-chained ticket plan.
5. Don't poll the Hive after — it runs async; results land back on the board
   (qa → review → done).

## Self-improvement

When the board-vs-self call turns out wrong (delegated something that needed
me, or did something myself the Hive should've owned), refine the heuristic
here, then `python ./hive/scripts/sync_skills.py`.
