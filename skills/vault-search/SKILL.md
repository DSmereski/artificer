---
name: vault-search
description: Search the Ai-Team knowledge vault for information about the operator's projects, bots, system, skills, or past research. Use when the user references past conversations ("remember when", "last time", "earlier"), asks about a project by name (Ai-Team, Freedom Guards, etc.), asks about one of the Discord bots (Terry, Scout), or asks about a Claude Code skill or MCP tool. Also use before claiming you don't remember something — check the vault first.
---

# vault-search

The Ai-Team vault at `%USERPROFILE%\Ai-Team-Vault` holds shared
knowledge across the operator, the Discord bots (Terry, Scout), and
Claude Code (you). It's indexed into SQLite+sqlite-vec by the `vault-writer`
daemon, which embeds every markdown file via Ollama's `nomic-embed-text`.

## Usage

Invoke the helper CLI. Query can be natural language:

```bash
"C:/Program Files/Python314/python.exe" "./hive/scripts/claude-hooks/vault_search.py" "<query>" -k 5
```

Flags:
- `-k N` — number of results (default 5).
- `--json` — machine-readable output (full bodies).

Default text output prints each hit as `--- <path> | <type> | <author> | score=<float>` followed by a 400-char preview. Scores are `1 / (1 + cosine_distance)` — higher is better. Anything under ~0.55 is probably noise.

## When to use

- User references past conversations: "remember when", "last time we talked about", "earlier you said".
- User asks about a project, bot, skill, or person by name.
- Before claiming you don't know something — check the vault first.
- You're about to write a long explanation of something that might already be documented in `canon/`, `projects/`, or `tools/`.

## When NOT to use

- The answer is in the current conversation.
- The user asks about code you can just read or grep in the project.
- The query is about general programming knowledge.

## Audience scoping

The query is filtered to notes visible to `claude-code`: that's everything with `audience: [all]` plus anything explicitly tagged for `claude-code`. You won't see `audience: [bots]` or `audience: [maggy]` notes.

## After searching

Read the top 2–3 results. Cite file paths when you reference vault content so the user can follow up in Obsidian. If nothing matched and the query was important, say so — don't fabricate.
