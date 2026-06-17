---
name: vault-remember
description: Save a note to the Ai-Team knowledge vault so it persists across Claude Code sessions and is visible to the Discord bots (audience-scoped). Use when you learn something worth keeping — a fact about the operator's projects, a system/environment detail, how a tool works, a web research finding, a the operator-specific collaboration preference, or a self-observation from this session. Prefer this over waiting for the Stop hook's auto-classifier when the fact is important and you want to be sure it lands.
---

# vault-remember

Writes a markdown note to the Ai-Team vault via the vault-writer daemon.
The daemon embeds the note, indexes it, and (eventually) pushes the commit
to Gitea. The note becomes visible to future Claude Code sessions via the
SessionStart preload and the `vault-search` skill, and — depending on
audience — to the Discord bots (Terry, Scout) during their 30-minute
canon refresh.

## Usage

```bash
"C:/Program Files/Python314/python.exe" "./hive/scripts/claude-hooks/vault_remember.py" <category> "<title>" "<body>"
```

Optional flags:
- `--audience all` (default) or `--audience claude-code` or `--audience bots` etc.
- `--tags foo bar baz`

## Categories and when to use each

- `knowledge` — web research, external facts, articles, paper findings.
- `system` — machine/environment facts (paths, GPUs, services, installed tools).
- `project` — facts about one of the operator's projects (use the project slug as title).
- `tool` — how a Claude Code skill, MCP tool, CLI command, or library works.
- `ops` — the operator's collaboration preferences or workflow rules. Defaults to
  `audience: [claude-code]` so it doesn't leak into the Discord bots' persona.
- `journal` — a noteworthy self-observation. Appended under a dated heading
  in `journals/claude-code.md`.
- `person` — facts about a Discord user. Use their Discord ID as `--tags`.

## When to use

- Before finishing a task, if you learned something non-obvious that would
  save time next session ("the vault path has a space, so bash-side cmd.exe
  invocation needs single-quote wrapping").
- When the operator corrects you ("stop doing X") — save as `ops`.
- When you resolve a non-trivial bug whose root cause isn't in the commit
  message.
- When a web fetch yields a reusable snippet of information.

## When NOT to use

- Trivial exchanges, greetings, LOL messages.
- Things already documented in CLAUDE.md, README, or the code itself.
- Things that are about *this exact task* only — the Stop hook will catch
  important session-scoped discoveries via its auto-classifier.

## Fail mode

If the daemon is down, the script returns exit code 1 with an error message.
You'll see `error: vault-writer daemon unreachable`. In that case, note the
fact in your final response so the operator can save it later, or use the
`openclaw-start` skill to restart the stack.
