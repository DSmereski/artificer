"""Run the research pipeline standalone and print every per-source
claim list + every group the deterministic corroborator builds.

Lets us see directly whether the extractor is returning ANY claims
(if so, the corroborator's threshold is the issue) or nothing
(extractor or fetcher is the issue).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Allow running directly via `python scripts/probe_research_pipeline.py`.
_PROJECT = Path(__file__).resolve().parent.parent
if str(_PROJECT) not in sys.path:
    sys.path.insert(0, str(_PROJECT))

from gateway.helpers.base import OllamaInvoker
from gateway.research_pipeline import (
    ResearchDeps, ddg_search, research, _group_corroborated_claims,
    _claim_tokens, _jaccard,
)
from gateway.safe_fetcher import safe_fetch


TOPIC = sys.argv[1] if len(sys.argv) > 1 else "Drake Cutlass Black Star Citizen"


async def main() -> int:
    invoker = OllamaInvoker()

    async def llm(system: str, user: str, params: dict | None) -> str:
        text, _, _ = await invoker.chat(
            model="planner-qwen", system=system, user=user,
            params=params or {},
        )
        return text

    deps = ResearchDeps(
        search=lambda t, k: ddg_search(t, k=k),
        fetch=lambda u: safe_fetch(u),
        llm=llm,
    )

    # Manually walk the pipeline so we can dump per-source claims.
    from gateway.research_pipeline import _EXTRACT_SYSTEM
    from gateway.helpers.base import parse_with_schema
    from gateway.research_pipeline import ClaimList

    print(f">> Topic: {TOPIC}")
    urls = await deps.search(TOPIC, 5)
    print(f">> DDG returned {len(urls)} URLs:")
    for u in urls:
        print(f"   {u}")

    fetched = await asyncio.gather(*[deps.fetch(u) for u in urls])
    sources = [f for f in fetched if f and f.text]
    print(f"\n>> Fetched {len(sources)} sources successfully:")
    for s in sources:
        print(f"   {s.url_final} ({len(s.text)} chars)")

    if len(sources) < 2:
        print("Not enough sources to corroborate. Bailing.")
        return 1

    print("\n>> Per-source extraction:")
    claim_lists = []
    for i, s in enumerate(sources):
        body = s.text[:6000]
        user_msg = f"<TOPIC>{TOPIC}</TOPIC>\n<UNTRUSTED_SOURCE>{body}</UNTRUSTED_SOURCE>"
        try:
            text = await deps.llm(_EXTRACT_SYSTEM, user_msg, None)
            cl = parse_with_schema(text, ClaimList)
            claim_lists.append((s, list(cl.claims)))
            print(f"\n   [{i}] {s.url_final}: {len(cl.claims)} claims")
            for j, c in enumerate(cl.claims):
                txt = c.get("claim") if isinstance(c, dict) else str(c)
                print(f"     {j}: {txt[:140]}")
        except Exception as e:
            print(f"   [{i}] {s.url_final}: EXTRACTION FAILED — {e}")
            claim_lists.append((s, []))

    print("\n>> Cross-source overlap matrix (Jaccard):")
    flat = []
    for i, (_, claims) in enumerate(claim_lists):
        for c in claims:
            txt = c.get("claim") if isinstance(c, dict) else str(c)
            if txt:
                flat.append((i, txt, _claim_tokens(txt)))
    print(f"   total flat claims: {len(flat)}")
    high = []
    for i, (s_a, t_a, k_a) in enumerate(flat):
        for s_b, t_b, k_b in flat[i + 1:]:
            if s_a == s_b:
                continue
            score = _jaccard(k_a, k_b)
            if score >= 0.20:
                high.append((score, s_a, t_a, s_b, t_b))
    high.sort(reverse=True)
    print(f"   pairs with overlap ≥0.20 (across different sources): {len(high)}")
    for score, sa, ta, sb, tb in high[:15]:
        print(f"     {score:.2f}  src{sa}: {ta[:70]!r}")
        print(f"           src{sb}: {tb[:70]!r}")

    print("\n>> Group output:")
    groups = _group_corroborated_claims(claim_lists)
    for g in groups:
        srcs = sorted({s for s, _ in g["members"]})
        marker = "FACT" if len(srcs) >= 2 else "note"
        print(f"   [{marker}] sources={srcs} consolidated={g['consolidated'][:90]!r}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
