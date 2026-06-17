# Karpathy Method — ready-to-paste prompts

Verbatim-style prompts for each layer. Adapt the bracketed parts.

## Layer 1 — Spec

**Goal-uncovering interview:**
```
Before proposing any solution, interview me one question at a time to identify
the real GOAL of [project] — the decision or conclusion it must drive, not just
the surface task. Keep asking until the goal is unambiguous, then restate it.
```

**Agile, compartmentalized spec:**
```
Now draft a spec for [project]. Bias toward smaller, compartmentalized chunks
with explicit checkpoints I review before you proceed. Tight scope per chunk.
Make me verify key decisions explicitly so nothing important is assumed.
```

## Layer 2 — Verifier

**Criteria up front:**
```
Before building anything, outline the precise evaluation criteria you will use
to judge the final product. Be specific and measurable (counts, structure,
pass/fail conditions) — not "looks good". List them so I can correct them.
```

**Second-model critic:**
```
When this turns into a complex build, run the final output past a second model
(a fresh adversarial reviewer / a different provider) and reconcile any
disagreement before calling it done.
```

**External signal:**
```
Don't guess whether it worked — verify against the real system: run the tests,
hit the deployed endpoint to confirm deployment, or load [prior artifact] as the
ground-truth reference for format/correctness. Report the actual signal.
```

## Layer 3 — Environment

**CLAUDE.md skeleton (minimum viable workshop — fill the four sections):**
```markdown
# <project> — working agreement

## How this repo works
<one-paragraph map: entry points, where code/tests/config live>

## Skills + routing
<which custom skills exist and when to invoke each>

## Knowledge architecture
<where information lives: docs/, the LLM knowledge base folder, the vault>

## Key working rules
- Before anything multi-step, include a verification plan.
- <always-do / ask-first / never-do rules; never-do should also have a hook>
```

**Environment audit:**
```
Audit my setup as a compounding workshop: (1) does CLAUDE.md cover how the repo
works, the skills + routing, where knowledge lives, and hard rules? (2) is there
an LLM knowledge base the agent can navigate? (3) what repeated work should
become a skill? (4) bucket every risky action into always-do / ask-first /
never-do, and for each never-do propose a PreToolUse hook that enforces it at
the tool level. Output concrete changes.
```

**Never-do hook (concept):**
```
For [/important paths or destructive commands], a CLAUDE.md request is
bypassable. Add a PreToolUse hook on Write/Edit/Bash that inspects the target
and blocks it — tool-level enforcement, not a prompt request. (Use the
update-config skill to write the settings.json hook.)
```
