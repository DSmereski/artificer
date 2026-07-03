# SPEC: Knowledge-Vault Index + Hybrid RAG Retrieval

Reimplementation-grade spec for the markdown-notes ingestion and hybrid
retrieval subsystem of `vault_writer`: watch → chunk → embed → store, and
hybrid (vector + keyword) search. A stranger with no access to the shipped
code should be able to rebuild an equivalent system from this document.

**Out of scope** (present in the source, sharing the same SQLite file, but
not part of note retrieval): the append-only chat-turn log, chat-thread
management, entity-graph "compiled truth" pages, and idle-time grooming
scanners (staleness/duplicate/format/link-rot).

## 1. Storage (one SQLite DB per vault, `sqlite-vec` + FTS5)

**`notes`** — one row per markdown file: `id` PK, `path` TEXT UNIQUE
(vault-relative), `note_type`, `author`, `audience` (JSON string array —
access-control tag, not searched), `frontmatter` (JSON), `body` (full
unchunked markdown), `updated_at`. No `content_hash` / `embed_status`
columns exist — see §2.4.

**`vec_notes`** — legacy single-vector table. `vec0` virtual table,
`embedding FLOAT[D]`, rowid = `notes.id`. Holds only chunk 0's vector
(kept for callers that want one vector per note).

**`note_chunks`** (`id` PK, `note_id`, `chunk_idx`, `UNIQUE(note_id,
chunk_idx)`) + **`vec_note_chunks`** (`vec0`, `embedding FLOAT[D]`, rowid =
`note_chunks.id`) — one row/vector per chunk of a note's body. Fully
replaced on every re-embed: `upsert_chunks(note_id, embeddings)` deletes all
existing chunk rows for that note, then re-inserts from `chunk_idx=0`
(delete-then-reinsert, not a diff).

**`notes_fts`** — `fts5(path UNINDEXED, title, body, tags, tokenize =
'porter unicode61')`. Not trigger-synced; the same upsert/delete call that
touches `notes` explicitly writes/removes the matching `notes_fts` row,
keyed by the same rowid. `title` = frontmatter title, else a readable form
of the filename stem. `tags` = frontmatter tags space-joined.

**`ingest_queue`** — durable write buffer: `id` PK, `payload` (JSON write
request), `state` (`pending→processing→done`, or back to `pending` on
retry, or `failed` after `MAX_ATTEMPTS=5`), `attempts`, `error`,
timestamps. A crash leaves rows stuck `processing`; startup resets those to
`pending`. Populated by inserting rows directly into the table — a durable
path distinct from the live RPC (§2.2) — and drained on a timer (§2.3).

**`wiki_reviews`** — human-review queue: `id` PK, `slug`, `kind` (CHECK IN
`contradiction`/`gap`/`stale`), `summary`, `source_notes` (JSON array),
`status` (CHECK IN `open`/`resolved`/`dismissed`, default `open`),
`created_at`, `resolved_at`. See §4.

## 2. Ingestion — three entry points, one write path

### 2.1 Filesystem watch (primary)

Recursive watch on `*.md` under the vault root. On create/modify: split
YAML frontmatter from body; resolve `note_type` from a fixed
folder→type table (e.g. `knowledge/`→`knowledge`, `people/`→`person`),
overridable by frontmatter `type:`. Notes under a `canon/` top folder are
rejected unless `author == "human"` (left on disk, not indexed). Dotfiles
ignored; files > 5 MiB skipped. The daemon's own writes are tagged with a
short-lived (~5s) in-memory marker so its own file writes don't re-trigger
via watcher echo.

**Chunk → embed → store**, synchronous per file:
1. `chunk_text(body, chunk_size, overlap)` splits the full body (§6 for
   constants).
2. Every chunk embedded with the model's "document" task prefix (§2.5).
3. Chunk 0's vector → `notes`/`vec_notes`/`notes_fts` (insert-or-update
   keyed on `path`).
4. All chunk vectors → `note_chunks`/`vec_note_chunks` (full replace,
   §1).

On delete/move-away, all five rows (`notes`, `notes_fts`, `vec_notes`,
`note_chunks`, `vec_note_chunks`) are removed in one call. On daemon
startup, **orphan reconciliation** diffs on-disk `*.md` paths against
`notes.path` and deletes any indexed row with no backing file (skipped
entirely if the on-disk scan finds zero files, to avoid wiping the index on
an unmounted/misconfigured vault path).

**Embed-failure circuit breaker**: in-memory per-path counter; after 3
consecutive embedding failures (e.g. a file that overflows the model's
context window) the path is skipped for the rest of the process lifetime.
Restart clears the counter.

### 2.2 Live write RPC ("learn")

Line-delimited JSON over TCP (loopback bind, optional shared-secret auth).
Renders/merges YAML frontmatter, writes the file atomically (temp file +
rename), then runs the same chunk→embed→store steps **synchronously**,
replying only once fully indexed. Wiki synthesis (§4) then runs in a
background thread so the RPC caller isn't blocked on an LLM call.

### 2.3 Durable queue drain

Every 2s, pull up to 20 `pending` rows from `ingest_queue`, replay each
through the same write path as §2.2 (`pending→processing→done`, or retry,
or `failed`). Wiki synthesis runs inline here (already off the RPC hot
path). One row's failure never blocks the batch.

### 2.4 No content-hash guard exists

A write request declares an `idempotency_key` field and the write response
declares a `deduped` boolean, but **neither is read or set anywhere** —
they are inert fields, not a wired dedup mechanism. No hash of note content
is stored, and nothing is compared against a prior hash before re-embedding.
Every filesystem "modified" event — including a no-op re-save with
byte-identical content — unconditionally re-runs the full chunk→embed→store
sequence. Add a stored content hash + comparison yourself if you want
skip-unchanged behavior; it is not present today.

### 2.5 Embedding call

Each chunk is sent as one request with a task prefix prepended:
`"search_document: "` for indexing, `"search_query: "` for query time
(asymmetric-embedding-model convention — omitting the prefix breaks the
index/query alignment the model was trained for). Response vector length is
validated against the configured dimension; a mismatch raises rather than
indexing a malformed vector.

## 3. Retrieval — hybrid search

Two rankers run over disjoint indexes and are fused. **No cross-encoder or
LLM rerank step** — the title-substring boost (§3.3) is the only re-ranking
beyond RRF.

**Vector half**: cosine-distance kNN over `vec_notes` (note-level) or
`vec_note_chunks` (chunk-level, §3.4), query embedded with the
`"search_query: "` prefix. Overscanned to `k * AUDIENCE_OVERSCAN_FACTOR`
before audience filtering (§3.5), so filtering never starves the final top-k.

**Keyword half**: query string tokenized (alphanumeric runs only) and
rejoined as a quoted-term FTS5 `MATCH` expression, joined with `OR` — not
`AND`, since natural-language queries would otherwise force every stray
preposition to match, measured to drop the correct note entirely. Ranked by
SQLite's built-in `bm25()` over `notes_fts`. A malformed/empty query
degrades to skipping this half (vector-only fallback), never a hard error.

### 3.3 Fusion — Reciprocal Rank Fusion (RRF)

Each ranker's results are ranked 1-indexed (best = rank 1). A candidate's
fused score sums `1 / (K + rank)` over every ranker that returned it:

```
score(note) = Σ_ranker  1 / (RRF_K + rank_ranker(note))
```

`RRF_K = 60` (§6). A note appearing in both rankers' results accumulates
score from both — there is no separate per-ranker weight. A candidate
missing from a ranker's results simply gets no term from it.

**Title-stem boost**: after fusion, the query is re-tokenized (drop tokens
< 3 chars, digit-only tokens, and a small stop-word list). For each
surviving candidate, count distinct query tokens that substring-match its
filename stem or frontmatter title; add `TITLE_BOOST * matched_count` to
its fused score (§6 — chosen to exceed any single ranker's max
per-candidate contribution of `1/(RRF_K+1)`, so a strong title match can
outrank a noisy rank-1 vector/keyword hit). Exists because short/rare
proper-noun queries can return semantically-unrelated top vector hits while
the keyword ranker finds the right note but loses a rank-1-vs-rank-1 tie —
the boost breaks that tie toward the title match.

Final order: sort by fused score descending, audience-filter (§3.5),
truncate to caller's `k`.

### 3.4 Per-chunk vs per-note fusion

`search_by_chunks` runs vector kNN against `vec_note_chunks` instead of
`vec_notes` (overscanned `4x` further, since many chunks compete per note),
then **max-pools to one row per note**: keep only each note's
lowest-distance chunk. That deduplicated, best-distance-per-note list is
ranked (rank 1 = smallest distance) and fed into the identical
RRF-plus-keyword-plus-title-boost fusion of §3.3 — note-level and
chunk-level search share one fusion implementation once reduced to one row
per note. Falls back transparently to note-level search (§3.1) if
`note_chunks` is empty (fresh index, nothing chunk-indexed yet).

### 3.5 Audience filter

Every candidate carries an `audience` list. A caller supplies its own
identity string; a candidate is visible if the caller's identity is a
wildcard value, OR the note's audience list contains the wildcard, OR the
caller's identity literally appears in the note's audience list (plus a
small built-in group-membership convenience for multi-agent deployments —
not required for a single-user rebuild). Filtering happens **after**
ranking on the already-overscanned set — the reason overscan exists (§6):
without it, a query where most top-k vector hits belong to a restricted
audience would return fewer than `k` visible results.

### 3.6 Relevance floor and default k — declared, not enforced here

Config exposes `default_k` (fan-out default) and a `min_score` **relative
relevance floor** (default `0.4`), but **neither is read inside this
package's search methods** — `search()`/`search_by_chunks()` always return
every ranked, audience-visible candidate up to the caller-supplied `k`, no
score cutoff. These values exist for an external caller to apply
post-hoc — e.g. drop hits below `min_score` of the top hit's score, or
default `k` when unspecified. **If rebuilding this, you must add the
cutoff yourself**; it is not enforced today. Given RRF scores are small (max
single-ranker contribution `1/61≈0.0164`; title boost adds `0.1` per
matched token), a literal `0.4` floor applied to raw fused scores would
reject nearly everything — treat it as *relative to the top result's
score*, not absolute, if you wire it up.

## 4. Wiki synthesis + review queue

After a note is written+indexed via §2.2/§2.3 (not the raw filesystem-watch
path), if synthesis is enabled:

1. **Retrieve context**: keyword-only search (zero vector passed) using the
   new note's first ~1000 chars as query, `top_k` results, filtered to
   paths under `wiki/`.
2. **ANALYZE** (LLM call 1): new note + retrieved wiki pages, both fenced
   as untrusted data in the prompt (defends against prompt injection from
   note content). Returns JSON: slug, title, entities, related existing
   wiki slugs, **contradictions** (claims conflicting with an existing wiki
   page), **gaps** (topics the note raises with no existing wiki coverage).
3. **Queue review items**: every contradiction/gap → a `wiki_reviews` row
   (`kind='contradiction'|'gap'`, `status='open'`, `source_notes=[path]`).
   A write failure here is caught/logged, never aborts synthesis.
4. **GENERATE** (LLM call 2): same fenced inputs + contradiction list →
   article body (wikilinks restricted to related slugs from step 2;
   contradictions surfaced as a visible callout, not asserted as fact).
5. **Write**: atomic write to `wiki/<slug>.md` (frontmatter: title,
   accumulating deduped `sources` list, `related`, `updated`); append a
   line to `wiki/log.md`; fully regenerate `wiki/index.md` as a catalog of
   every page under `wiki/`.

Fail-soft end-to-end: any exception is caught, logged, returns a failure
result — the original note write is never rolled back. `wiki_reviews` rows
are read via "list open, newest first" and mutated via
"resolve/dismiss" (`status→resolved|dismissed`, sets `resolved_at`),
intended to back an external "needs review" UI.

## 5. Limits

Title ≤ 200 chars, body ≤ 32 KiB, one wire request ≤ 128 KiB, on-disk note
file ≤ 5 MiB (oversized files skipped, not truncated). File writes are
always atomic (temp file + rename). `canon` category is rejected at the RPC
layer (human-only, filesystem-direct). Some categories (journal, session,
person) append a dated section to an existing file rather than overwriting.

## 6. Key constants

| constant | value | meaning |
|---|---|---|
| chunk size | 4000 chars | max chars/chunk; configurable, floor 256 |
| chunk overlap | 400 chars | shared chars between consecutive chunks |
| chunk boundary window | last 20% of chunk | prefer a blank-line split inside this window; hard split otherwise |
| embedding model | `nomic-embed-text` (default) | asymmetric document/query-prefixed model |
| embedding dimension | 768 (default) | must match model; validated on embed + DB open |
| embedding execution | forced CPU | avoids evicting larger GPU-resident models on shared hardware |
| `RRF_K` | 60 | RRF constant (standard value from the RRF paper) |
| `TITLE_BOOST` | 0.1 / matched token | exceeds max single-ranker contribution (`1/61≈0.0164`) |
| `AUDIENCE_OVERSCAN_FACTOR` | 4 | multiply requested `k` before audience filtering |
| `min_score` (relevance floor) | 0.4 (config default) | declared, **not enforced** inside this package (§3.6) |
| `default_k` | 5 (config default) | declared, **not enforced** inside this package |
| ingest queue batch / interval | 20 rows / 2s | drain loop |
| ingest queue max attempts | 5 | failures before a row is permanently `failed` |
| embed-failure circuit breaker | 3 consecutive failures | per-path, in-memory, resets on restart |
| self-write echo TTL | 5s | suppresses watcher re-trigger on the daemon's own writes |
| content-hash guard | **none** | every modify event fully re-embeds (§2.4) |

## 7. Verifiable checkpoints

1. **Write → dual-mode searchable.** Add a note with a distinctive made-up
   phrase. Confirm: a `notes` row exists; a *keyword* query (exact
   substring) returns it; a *paraphrased* query (same meaning, no shared
   words) also returns it; `note_chunks`/`vec_note_chunks` rows match
   `chunk_text()`'s expected chunk count for that body length.
2. **Edit re-embeds fully, unconditionally.** Change only the last
   paragraph of an indexed note. Confirm old `note_chunks` rows for that
   note are gone and new ones present from `chunk_idx=0`; a query matching
   only the new paragraph now returns the note; re-saving with **zero**
   content change still triggers a full re-embed (negative case proving no
   content-hash guard exists, §2.4).
3. **Delete removes from every table.** Delete a note's file. Confirm rows
   are gone from all five tables in §1, both via live watch and via orphan
   reconciliation when the delete happens while the daemon is stopped.
4. **Title boost changes ranking outcome.** Create note A with a rare
   made-up token in its title/filename and unrelated body text; note B with
   a generic title but body semantically close to a test query. Query for
   the rare token. Confirm note A ranks first — proving the fused score
   with the title boost differs from (and reorders) vector-kNN-only
   ranking.
5. **Contradiction reaches the review queue.** With synthesis enabled and a
   working LLM backend, write a note that plainly contradicts an existing
   `wiki/<slug>.md` page. Confirm a new `wiki_reviews` row appears
   (`kind='contradiction'`, `status='open'`, `source_notes` containing the
   new note's path) and the wiki page is rewritten with a visible
   contradiction callout rather than silently asserting the new claim.
