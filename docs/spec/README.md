# Specs — rebuild the whole system from these, without trusting the code

**You do not have to trust this repository's code.** These specs describe every
component in enough detail — data models, database schemas, API contracts,
algorithms, and the exact constants that make each part behave — that a
competent developer (or a coding agent) can **reimplement an equivalent system
from the markdown alone**, in a language and stack of their choosing, and never
run a line of the shipped code.

That's the point. If you don't trust binaries or someone else's Python, build
your own from the specs and compare behavior against the checkpoints. The specs
are the contract; the code is just one implementation of it.

## The specs

| Spec | Covers | Lines |
|---|---|---|
| [SPEC-crew-board](SPEC-crew-board.md) | Ticket data model, SQLite schema (DDL-level), the lane state machine, the full `/board/*` HTTP API, goal-decompose. | 414 |
| [SPEC-build-loop](SPEC-build-loop.md) | The agent loop's tool contracts + sandboxing, the "honest done" verifier gates, the dispatcher (claim/serialize/rollback/reap), the escalation ladder. | 347 |
| [SPEC-vault-rag](SPEC-vault-rag.md) | The vault index schema, chunk/embed pipeline, hybrid (vector + BM25) retrieval with the RRF formula + constants, wiki synthesis + review queue. | 289 |
| [SPEC-model-router](SPEC-model-router.md) | The catalog format, the role→model map + resolution order, catalog-vs-installed reconciliation, the bench harness, the no-auto-pull contract. | 189 |
| [SPEC-dashboard](SPEC-dashboard.md) | The panel plugin contract, layout engine, WS+poll ingress, the 19 panels + their endpoints, the single-accent theming model. | 385 |
| [SPEC-install-and-runtime](SPEC-install-and-runtime.md) | Installer (GPU→model-tier detect), config-from-templates, the two-layer self-heal + loopback bind invariant, the companion app, the STT/command/TTS voice contracts. | 323 |

Companion: [BLUEPRINT.md](../BLUEPRINT.md) gives the **build order** (which
component to build first and the checkpoint to hit before the next). Read the
blueprint for the sequence, then each spec for the detail.

## How to use them to build-your-own

1. Read [BLUEPRINT.md](../BLUEPRINT.md) for the phase order (foundation → vault →
   board → verifier → dispatcher → router → dashboard → self-heal).
2. Pick a phase, open its spec, implement the data model + contracts in your
   stack. Every spec ends with **verifiable checkpoints** — build to those.
3. Compare your implementation's behavior against the checkpoints and, if you
   want, against this repo's endpoints. Same contracts → same behavior.
4. You never had to trust the code — only the specs, which you can read in full.

## Scope note (honest)

These specs describe **this repository's** (public, genericized) code as it
stands. Where the spec authors found the code diverging from folklore — e.g. a
declared-but-unused field, or a role-pull API that exists privately but not in
this public tree — they documented the **code's actual behavior**, not the
intent, and flagged the gap. Trust the spec over any prose claim; trust a
checkpoint over the spec.
