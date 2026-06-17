"""Hive capability ladder — run progressively harder tasks until
planner-qwen fails. Each tier is a fresh project, fresh loop, fresh
LLM context. Stops at first failure; prints the ladder so far.

Tiers:
  1. fizzbuzz.py + test_fizzbuzz.py (1 source, 1 test, 3 cases)
  2. calculator.py + test_calculator.py (4 ops, 4 tests)
  3. todo.py + test_todo.py (data class + 5 methods)
  4. cards/cards.py + cards/__init__.py + test_cards.py (2-file pkg)
  5. (extension point — add more)

Each tier:
  - Wipes its project dir
  - Spawns the hive agent loop with max_iters=40
  - Runs pytest at the end; reports passed/failed
  - Counts as success if all tests pass

Run:
    python scripts/spawn_tier_eval.py [--start N] [--stop N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from gateway.crew_board.store import CrewBoardStore, Project
from gateway.crew_board import schema
from gateway.crew_board.hive_agent_loop import run_hive_agent_loop

import os as _os
VAULT_DB = Path(_os.environ.get("HIVE_VAULT_PATH", "./vault")) / ".vault-writer" / "vault.db"
TRANSCRIPT_DIR = Path(_os.environ.get("HIVE_TRANSCRIPT_DIR", "./transcripts"))


class Tier:
    def __init__(
        self, *, num: int, name: str, dir_name: str,
        body: str, files: list[str],
    ) -> None:
        self.num = num
        self.name = name
        self.dir = Path(_os.environ.get("HIVE_PROJECTS_ROOT", "./projects")) / dir_name
        self.body = body
        self.files = files


TIERS = [
    Tier(
        num=1, name="fizzbuzz", dir_name="TierFizzbuzz",
        body="""Single-file FizzBuzz utility.

Layout:
  - fizzbuzz.py — module with `fizzbuzz(n: int) -> str`:
        n % 15 == 0 -> "FizzBuzz"
        n % 3 == 0  -> "Fizz"
        n % 5 == 0  -> "Buzz"
        else        -> str(n)
  - test_fizzbuzz.py — at root, imports `from fizzbuzz import fizzbuzz`

Tests to satisfy (each as its own test function):
  - test_fizz(): fizzbuzz(3) == 'Fizz'
  - test_buzz(): fizzbuzz(5) == 'Buzz'
  - test_fizzbuzz(): fizzbuzz(15) == 'FizzBuzz'
  - test_number(): fizzbuzz(7) == '7'

Make `python -m pytest -q` show 4 passed.""",
        files=["fizzbuzz.py", "test_fizzbuzz.py"],
    ),
    Tier(
        num=2, name="calculator", dir_name="TierCalc",
        body="""Single-file calculator.

Layout:
  - calc.py — module with `add(a, b)`, `sub(a, b)`, `mul(a, b)`,
    `div(a, b)`. div raises ValueError on zero divisor.
  - test_calc.py — at root, imports `from calc import add, sub, mul, div`

Tests:
  - test_add: add(2, 3) == 5
  - test_sub: sub(10, 4) == 6
  - test_mul: mul(3, 7) == 21
  - test_div_ok: div(20, 4) == 5
  - test_div_zero: pytest.raises(ValueError, div, 5, 0)

Make `python -m pytest -q` show 5 passed.""",
        files=["calc.py", "test_calc.py"],
    ),
    Tier(
        num=3, name="todo", dir_name="TierTodo",
        body="""Single-file TODO list.

Layout:
  - todo.py — `TodoList` class with:
        add(text: str) -> int (returns task id, monotonic from 1)
        complete(task_id: int) -> bool (True if found and toggled)
        pending() -> list[str]   (texts of incomplete tasks)
        completed() -> list[str] (texts of done tasks)
  - test_todo.py — at root, imports `from todo import TodoList`

Tests (5 total):
  - test_add_returns_id_1_first
  - test_pending_lists_only_open
  - test_complete_moves_to_done
  - test_complete_unknown_returns_false
  - test_ids_are_unique_and_monotonic

Make `python -m pytest -q` show 5 passed.""",
        files=["todo.py", "test_todo.py"],
    ),
    Tier(
        num=5, name="cards-hand", dir_name="TierCardsHand",
        body="""Three-file Python package: cards + hand.

Layout (flat, no src/):
  - bj/__init__.py — empty
  - bj/cards.py — Suit, Rank enums (13 distinct ranks; ACE=14), Card
    dataclass, build_deck() -> list[Card] returns 52 unique.
  - bj/hand.py — `Hand` class with:
        add(card: Card) -> None
        total() -> int (correctly handles A=1 or A=11)
        is_soft() -> bool (any ace counted as 11 in current total)
        is_blackjack() -> bool (2 cards totalling 21)
    Use a BLACKJACK_VALUE dict for J/Q/K = 10, ACE base = 11.
  - test_cards.py — at root, imports `from bj.cards import build_deck`
  - test_hand.py — at root, imports `from bj.hand import Hand` plus
    Card/Suit/Rank.

Tests (8 total):
  - test_cards.py:
    - test_deck_has_52_unique_cards
    - test_all_ranks_present (4 per rank)
  - test_hand.py:
    - test_two_cards_sum: 2+3 -> total 5
    - test_face_is_ten: K+5 -> 15
    - test_soft_18: A+7 -> 18 and is_soft() True
    - test_hard_17: A+7+9 -> 17 (Ace demoted) and is_soft() False
    - test_natural_blackjack: A+K -> 21 and is_blackjack() True
    - test_three_card_21_not_blackjack: 7+7+7 -> 21 but is_blackjack() False

Make `python -m pytest -q` show 8 passed.

Python gotcha: do NOT give enum members duplicate values — they
become aliases. Rank should be TWO=2 ... TEN=10, JACK=11, QUEEN=12,
KING=13, ACE=14 (sentinel values). Use a separate VALUE dict for
blackjack point values.""",
        files=["bj/__init__.py", "bj/cards.py", "bj/hand.py",
               "test_cards.py", "test_hand.py"],
    ),
    Tier(
        num=6, name="bj-core", dir_name="TierBjCore",
        body="""Four-file Blackjack core (no CLI yet).

Layout (flat, no src/):
  - bj/__init__.py — empty
  - bj/cards.py — Suit, Rank (13 distinct, ACE=14), Card dataclass,
    Deck class (init shuffles, deal() pops one card),
    build_deck() -> list[Card] returns 52.
  - bj/hand.py — Hand with add, total, is_soft, is_blackjack (as in
    tier 5).
  - bj/game.py — `play_dealer(dealer_hand: Hand, deck: Deck) -> None`
    that mutates dealer_hand following the policy:
      * hit while total < 17
      * hit on SOFT 17 (total 17 with soft ace)
      * stand on hard 17 or 18+
  - test_cards.py, test_hand.py, test_game.py — at root.

Tests (10 total):
  - cards: deck has 52 unique cards; Deck.deal returns one Card and
    shrinks the deck.
  - hand: soft 18 (A+7), hard 17 (A+7+9), natural blackjack (A+K),
    three-card 21 is NOT blackjack.
  - game: dealer hits on soft 17 (A+6 + forced card to push past 17),
    dealer stands on hard 17 (10+7), dealer hits on 16.

Make `python -m pytest -q` show 10 passed.

Reminders:
  - Distinct enum values (no aliases).
  - Use a VALUE map for blackjack values, separate from Rank's
    sentinel int values.
  - Hand.total() must correctly demote Ace from 11 to 1 when needed.""",
        files=["bj/__init__.py", "bj/cards.py", "bj/hand.py", "bj/game.py",
               "test_cards.py", "test_hand.py", "test_game.py"],
    ),
    Tier(
        num=4, name="cards-pkg", dir_name="TierCards",
        body="""Two-file Python package.

Layout (flat — package at root, NOT src/):
  - cards/__init__.py — empty
  - cards/deck.py — `Suit` and `Rank` enums (13 distinct ranks each
    with a UNIQUE value; use separate VALUE map for blackjack value),
    `Card(suit, rank)` dataclass, `build_deck() -> list[Card]` returns
    52 unique Card instances.
  - test_deck.py — at root, `from cards.deck import Suit, Rank, Card, build_deck`

Tests (4 total):
  - test_deck_has_52_unique_cards: len(set(build_deck())) == 52
  - test_all_suits_present: 13 cards per suit
  - test_all_ranks_present: 4 cards per rank
  - test_card_repr: str(Card(Suit.HEARTS, Rank.ACE)) contains both names

Python gotcha: do NOT give enum members duplicate values — they
become aliases. Rank should be TWO=2 ... TEN=10, JACK=11, QUEEN=12,
KING=13, ACE=14 (sentinel values, not blackjack values).

Make `python -m pytest -q` show 4 passed.""",
        files=["cards/__init__.py", "cards/deck.py", "test_deck.py"],
    ),
    Tier(
        num=7, name="bj-full", dir_name="TierBjFull",
        body="""Five-file Blackjack package: cards + hand + game (full
dealer policy) + cli (importable, not interactive).

Layout (flat):
  - bj/__init__.py — empty
  - bj/cards.py — Suit, Rank (13 distinct, ACE=14), Card dataclass,
    Deck class (init shuffles, deal() pops one card), build_deck().
  - bj/hand.py  — Hand: add, total (correct A=1 or A=11), is_soft,
    is_blackjack, is_bust.
  - bj/game.py  — Game class:
        - resolve(player: Hand, dealer: Hand) -> str returns one of
          'player', 'dealer', 'push'. Natural blackjack on player
          beats any dealer non-natural.
        - play_dealer(dealer: Hand, deck: Deck) -> None mutates
          dealer in place: hit while total < 17, hit on soft 17.
  - bj/cli.py — `play_round(deck, get_choice) -> str` callable: gets
    initial deal, prompts via the get_choice callable ('h' or 's'),
    returns the resolve() winner. The function takes get_choice so
    tests can pump stand/hit programmatically without stdin.

Tests (at least 14, in tests/test_cards.py, test_hand.py,
test_game.py, test_cli.py):
  - cards: 52 unique deck, deal pops one card and shrinks
  - hand: 2+3=5; K+5=15; A+7=18 soft; A+7+9=17 hard; A+K natural;
    7+7+7=21 not natural; bust at 22+
  - game.resolve: player natural beats dealer 21 in 3 cards;
    player bust loses regardless; both natural -> push;
    equal totals -> push; higher total wins
  - game.play_dealer: hits soft 17, stands hard 17, hits 16
  - cli.play_round: stand on initial 20 -> 'player' or 'push'
    depending on dealer; hit until bust -> 'dealer'

Reminders:
  - Distinct enum values (no aliases).
  - Use a VALUE map for blackjack point values, separate from Rank.
  - Hand.total() must correctly demote Ace from 11 to 1 when needed.

Make `python -m pytest -q` show 14+ passed.""",
        files=[
            "bj/__init__.py", "bj/cards.py", "bj/hand.py",
            "bj/game.py", "bj/cli.py",
            "tests/test_cards.py", "tests/test_hand.py",
            "tests/test_game.py", "tests/test_cli.py",
        ],
    ),
    Tier(
        num=8, name="ds-core", dir_name="TierDataStructures",
        body="""Four-file data structures library.

Layout (flat):
  - ds/__init__.py — empty
  - ds/linked_list.py — `LinkedList` with append(x), prepend(x),
    pop_front() -> value, __len__, __iter__ (yields values in order).
  - ds/min_heap.py — `MinHeap` with push(x), pop() -> min, peek(),
    __len__. Use a list-backed binary heap (do NOT just sort each time).
  - ds/bst.py — `BST` with insert(x), contains(x) -> bool,
    in_order() -> list (sorted ascending). No balancing required.
  - tests/test_linked_list.py, test_min_heap.py, test_bst.py at root.

Tests (at least 12):
  - linked_list: append + iter; prepend + iter; pop_front returns
    first then shrinks; len after mixed ops
  - min_heap: peek == min; pop returns ascending sequence after
    multi-push; randomised push/pop ordering check
  - bst: insert + contains; in_order returns sorted; missing element
    contains() False; reinsert is idempotent or duplicate-tolerant

Avoid hidden gotchas:
  - Don't use heapq inside MinHeap — re-implement bubble-up/down.
  - Don't store the heap as a sorted list (O(n log n) per push).

Make `python -m pytest -q` show 12+ passed.""",
        files=[
            "ds/__init__.py", "ds/linked_list.py",
            "ds/min_heap.py", "ds/bst.py",
            "tests/test_linked_list.py", "tests/test_min_heap.py",
            "tests/test_bst.py",
        ],
    ),
    Tier(
        num=9, name="csv-json", dir_name="TierCsvJson",
        body="""Three-file CSV → JSON transform with validation.

Layout (flat):
  - transform/__init__.py — empty
  - transform/parser.py — `parse_csv(text: str) -> list[dict]` that
    treats the first row as headers, strips whitespace, parses
    numeric columns as int OR float when they look numeric (use try
    int -> try float -> str fallback). Empty cells become None.
  - transform/writer.py — `to_json(rows: list[dict], pretty: bool =
    False) -> str` that JSON-encodes the rows; pretty=True uses
    indent=2.
  - transform/cli.py — `convert(csv_text: str) -> str` chains parse
    + to_json(pretty=True).
  - tests/test_parser.py, test_writer.py, test_cli.py at root.

Tests (at least 10):
  - parser: returns list of dicts keyed by headers; numeric coercion
    int + float; missing cells -> None; trims whitespace; empty
    string input -> []
  - writer: pretty=True produces multi-line JSON containing indent;
    pretty=False is compact; round-trip equivalence on simple rows
  - cli.convert: end-to-end correctness on a small sample; output
    is valid JSON (round-trippable via json.loads)

Hints:
  - csv stdlib is fine; you do NOT need pandas.
  - Don't over-engineer: a small handwritten state machine for parse
    is overkill — csv.DictReader handles header + rows in 3 lines.

Make `python -m pytest -q` show 10+ passed.""",
        files=[
            "transform/__init__.py", "transform/parser.py",
            "transform/writer.py", "transform/cli.py",
            "tests/test_parser.py", "tests/test_writer.py",
            "tests/test_cli.py",
        ],
    ),
    Tier(
        num=10, name="event-bus", dir_name="TierEventBus",
        body="""Single-file event bus with concurrency-aware semantics.

Layout (flat):
  - bus/__init__.py — empty
  - bus/event_bus.py — `EventBus` class with:
        subscribe(topic: str, handler: Callable[[Any], None]) -> int
            returns a subscription id
        unsubscribe(sub_id: int) -> bool
            True if removed
        publish(topic: str, payload: Any) -> int
            returns the number of handlers invoked (synchronous)
        topics() -> list[str]
            currently-subscribed topic names, no duplicates, sorted
    Handlers are stored per-topic; a single subscriber can subscribe
    to multiple topics independently. Multiple subscribers on the
    same topic all run on publish, in subscription order. Exceptions
    from one handler must NOT prevent later handlers running; failed
    handlers' exceptions are captured but do not propagate.
  - tests/test_event_bus.py at root.

Tests (at least 10):
  - subscribe + publish reaches the handler exactly once
  - multiple subscribers on same topic all fire, in order
  - subscriber on topic A is unaffected by publish to topic B
  - unsubscribe removes handler; further publishes don't call it
  - unsubscribe with bad id returns False
  - publish to topic with no subscribers returns 0 and doesn't raise
  - handler raising still allows later handlers on same topic to run
  - topics() returns sorted unique names after multi-subscribe
  - subscribe returns monotonically-increasing ids
  - publish payload reaches handler unchanged (identity check)

Avoid: don't pull in third-party reactive libs; stdlib only.

Make `python -m pytest -q` show 10+ passed.""",
        files=[
            "bus/__init__.py", "bus/event_bus.py",
            "tests/test_event_bus.py",
        ],
    ),
]


async def run_tier(
    tier: Tier, *, max_iters: int,
    model: str | None = None,
    transcript_path_override: Path | None = None,
    project_dir_override: Path | None = None,
) -> dict:
    """Run one tier through the hive agent loop.

    `model` — Ollama model tag; defaults to the loop's default
    (planner-qwen). When the benchmark runner sweeps many models, this
    is set per call.
    `transcript_path_override` — if set, the loop writes its
    JSON-per-turn transcript here instead of the default per-tier path.
    `project_dir_override` — if set, work in this directory instead of
    `tier.dir` (used so each model gets its own workspace).
    """
    print(f"\n=== TIER {tier.num}: {tier.name} "
          f"(model={model or 'default'}) ===")
    project_dir = project_dir_override or tier.dir
    # Fresh project dir
    if project_dir.exists():
        shutil.rmtree(project_dir, ignore_errors=True)
    project_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=project_dir, check=True)

    store = CrewBoardStore(VAULT_DB)
    # The project slug must be unique per (model, tier) when sweeping
    # so the store doesn't merge runs. Model-scoped slug for benchmark
    # runs, plain slug for the original eval.
    if model is not None:
        model_slug = model.replace(":", "-").replace("/", "-").replace("_", "-")
        proj_slug = f"bench-{model_slug}-tier{tier.num}"
    else:
        proj_slug = f"tier{tier.num}-{tier.name}"
    proj = Project(
        slug=proj_slug,
        path=str(project_dir).replace("\\", "/"),
        name=project_dir.name,
        enabled=True, push_allowed=False,
        test_cmd="python -m pytest -q",
    )
    store.upsert_project(proj)
    task = store.create_task(
        project_slug=proj.slug,
        title=f"Tier {tier.num}: {tier.name}",
        body=tier.body,
        created_by="owner",
        files_of_interest=tier.files,
        acceptance_criteria=[{"text": "python -m pytest -q passes"}],
        tags=["hive", "tier-eval"] + (["benchmark", model] if model else []),
    )
    store.assign_task(task.slug, "hive", actor="owner")
    store.move_task(task.slug, schema.STATUS_READY, actor="owner",
                    detail="ready")
    store.move_task(task.slug, schema.STATUS_IN_PROGRESS, actor="hive",
                    detail="start")
    task = store.get_task(task.slug)

    transcript_path = transcript_path_override or (
        TRANSCRIPT_DIR / f"tier{tier.num}_transcript.json"
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    loop_kwargs = {
        "max_iters": max_iters,
        "transcript_path": transcript_path,
    }
    if model is not None:
        loop_kwargs["model"] = model
    result = await run_hive_agent_loop(store, task, **loop_kwargs)
    dt = time.monotonic() - t0
    # Archive the throwaway eval task immediately. run_tier drives the
    # hive loop DIRECTLY (not via the shared dispatcher), so this board
    # task is just a bookkeeping record. If left ready/in_progress, a
    # running CrewDispatcher (e.g. the SC gameplay driver) would claim
    # it as real work — that's how 16 bench tasks leaked onto the board.
    try:
        cur = store.get_task(task.slug)
        if cur and cur.status not in (schema.STATUS_ARCHIVED,):
            for step in (schema.STATUS_REVIEW, schema.STATUS_DONE,
                         schema.STATUS_ARCHIVED):
                try:
                    store.move_task(task.slug, step, actor="system",
                                    detail="eval bookkeeping; archived")
                except ValueError:
                    continue
    except Exception:  # noqa: BLE001
        pass
    # Run pytest independently of the agent's `done` claim
    pt = subprocess.run(
        ["python", "-m", "pytest", "-q", "--tb=no"],
        cwd=str(project_dir), capture_output=True, text=True, timeout=60,
    )
    out = pt.stdout + pt.stderr
    import re
    m_pass = re.search(r"(\d+) passed", out)
    m_fail = re.search(r"(\d+) failed", out)
    passed = int(m_pass.group(1)) if m_pass else 0
    failed = int(m_fail.group(1)) if m_fail else 0
    rc = pt.returncode
    # Test-quality gate: codestral was caught writing stub tests with
    # bare `pass` in the v1 bench. AST-scan every test file and refuse
    # a tier-pass if any test_* function has zero asserts.
    asserts_audit = _audit_test_asserts(project_dir)
    print(f"[tier{tier.num}] loop done={result.ok} turns={result.turns} "
          f"dt={dt:.1f}s  pytest rc={rc} passed={passed} failed={failed} "
          f"asserts_ok={asserts_audit['ok']} "
          f"stub_tests={asserts_audit['stub_count']}")
    return {
        "tier": tier.num, "name": tier.name,
        "loop_ok": result.ok, "turns": result.turns,
        "dt_s": round(dt, 1),
        "pytest_rc": rc, "passed": passed, "failed": failed,
        "asserts_audit": asserts_audit,
        "tier_pass": (
            rc == 0 and passed > 0 and failed == 0
            and asserts_audit["ok"]
        ),
    }


def _audit_test_asserts(project_dir: Path) -> dict:
    """Walk every test file in the project; require each test_*
    function to contain at least one `assert` (or `pytest.raises`,
    which Codestral could otherwise weasel through). Returns a dict
    with `ok` and a list of stub function names. Empty test files are
    not a stub themselves — they're a count-zero pass."""
    import ast
    stubs: list[str] = []
    files_checked = 0
    funcs_checked = 0
    if not project_dir.is_dir():
        return {"ok": True, "stub_count": 0, "stubs": [],
                "files_checked": 0, "funcs_checked": 0,
                "reason": "no project dir"}
    for p in project_dir.rglob("test_*.py"):
        try:
            src = p.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src, filename=str(p))
        except (OSError, SyntaxError):
            continue
        files_checked += 1
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            funcs_checked += 1
            has_assert = False
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assert):
                    has_assert = True
                    break
                # pytest.raises(...) context-manager / call
                if isinstance(sub, ast.Attribute) and sub.attr == "raises":
                    has_assert = True
                    break
            if not has_assert:
                rel = p.relative_to(project_dir).as_posix()
                stubs.append(f"{rel}::{node.name}")
    return {
        "ok": len(stubs) == 0,
        "stub_count": len(stubs),
        "stubs": stubs[:20],  # cap for results.json sanity
        "files_checked": files_checked,
        "funcs_checked": funcs_checked,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--stop", type=int, default=len(TIERS))
    ap.add_argument("--max-iters", type=int, default=40)
    args = ap.parse_args()
    import logging
    logging.basicConfig(level=logging.WARNING)

    summary = []
    # TIERS declared out of order; sort by .num so the ladder climbs
    # 1->2->3->4->5->6.
    for tier in sorted(TIERS, key=lambda t: t.num):
        if tier.num < args.start: continue
        if tier.num > args.stop: continue
        r = await run_tier(tier, max_iters=args.max_iters)
        summary.append(r)
        if not r["tier_pass"]:
            print(f"[ladder] Tier {tier.num} FAILED; stopping climb.")
            break
        print(f"[ladder] Tier {tier.num} PASSED.")
    print("\n=== LADDER SUMMARY ===")
    for r in summary:
        mark = "PASS" if r["tier_pass"] else "FAIL"
        print(f"  tier{r['tier']} ({r['name']}): {mark}  "
              f"turns={r['turns']} dt={r['dt_s']}s "
              f"pytest={r['passed']}p/{r['failed']}f")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
