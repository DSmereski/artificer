"""Build the helper pool from a ModelCatalog.

Each entry in `catalog.helpers` is matched against the right helper
class and instantiated with the configured model + prompt + schema.
"""

from __future__ import annotations

import logging

from gateway.helpers.base import Helper, OllamaInvoker
from gateway.helpers.chat_recall import ChatRecallHelper
from gateway.helpers.coder import CoderHelper
from gateway.helpers.critic import CriticHelper
from gateway.helpers.fact_extractor import FactExtractorHelper
from gateway.helpers.image_director import ImageDirectorHelper
from gateway.helpers.librarian import LibrarianHelper
from gateway.helpers.planner import PlannerHelper
from gateway.helpers.researcher import ResearcherHelper
from gateway.helpers.shapes import shape_for
from gateway.helpers.skill_runner import SkillRunnerHelper
from gateway.helpers.summarizer import SummarizerHelper
from gateway.helpers.synthesizer import SynthesizerHelper
from gateway.helpers.sysmon import SysmonHelper
from gateway.model_catalog import ModelCatalog, ModelEntry

log = logging.getLogger("gateway.helpers.factory")


def _resolve_model_entry(
    catalog: ModelCatalog,
    role: str,
    h_entry,
    router,
) -> "ModelEntry":
    """Pick the ModelEntry to bake into the helper for `role`.

    When a Router is supplied and has bench data for this role, use its
    top pick — that's how a fast/cheap model gets routed to simple roles
    (chat_recall, relevance_gate) while keeping the strong model on
    complex ones (planner, coder). When bench data is missing the
    router falls back to the YAML default internally; we still defend
    against bad router state by catching and falling through to the
    catalog default here.
    """
    if router is not None:
        try:
            choice = router.route_for(role)
            log.debug(
                "router picked %s for role %s (%s)",
                choice.model.id, role, choice.reason,
            )
            return choice.model
        except Exception as e:  # noqa: BLE001
            log.debug(
                "router.route_for(%s) failed: %s — using YAML default",
                role, e,
            )
    return catalog.model(h_entry.model)


_HELPER_CLASSES: dict[str, type] = {
    "planner": PlannerHelper,
    "coder": CoderHelper,
    "researcher": ResearcherHelper,
    "image_director": ImageDirectorHelper,
    "sysmon": SysmonHelper,
    "summarizer": SummarizerHelper,
    "critic": CriticHelper,
    "librarian": LibrarianHelper,
    "synthesizer": SynthesizerHelper,
    "skill_runner": SkillRunnerHelper,
    "chat_recall": ChatRecallHelper,
    "fact_extractor": FactExtractorHelper,
}


def build_helpers(
    catalog: ModelCatalog,
    invoker: OllamaInvoker | None = None,
    skill_registry=None,
    vault_client_factory=None,
    ollama_url: str | None = None,
    router=None,
) -> dict[str, Helper]:
    """Construct one helper per role in the catalog. Roles whose model
    is unavailable are still constructed — the helper will return a
    HelperResult with `error` set when invoked, which is the loud-fail
    behaviour the catalog promises.

    `skill_registry`, when provided, is wired into the SkillRunner
    helper so it can resolve skill names to bodies + constraints.
    """
    invoker = invoker or OllamaInvoker()
    pool: dict[str, Helper] = {}
    for role in catalog.helper_roles:
        cls = _HELPER_CLASSES.get(role)
        if cls is None:
            log.warning("no helper class registered for role %r — skipping", role)
            continue
        h_entry = catalog.helper(role)
        m_entry = _resolve_model_entry(catalog, role, h_entry, router)
        try:
            schema = shape_for(h_entry.output_schema)
        except KeyError:
            log.warning(
                "helper %r references unknown schema %r — using None",
                role, h_entry.output_schema,
            )
            schema = None
        # Merge model-level + helper-level params; helper wins.
        merged_params = dict(m_entry.params)
        merged_params.update(dict(h_entry.params_override))
        kwargs = dict(
            model_id=m_entry.id,
            ollama_name=m_entry.ollama_name,
            prompt_name=h_entry.system_prompt_file,
            params=merged_params,
            invoker=invoker,
            timeout_s=h_entry.timeout_s,
            schema=schema,
        )
        if role == "skill_runner" and skill_registry is not None:
            kwargs["registry"] = skill_registry
        if role == "librarian":
            if vault_client_factory is not None:
                kwargs["vault_client_factory"] = vault_client_factory
            if ollama_url is not None:
                kwargs["ollama_url"] = ollama_url
        if role == "chat_recall" and vault_client_factory is not None:
            kwargs["vault_client_factory"] = vault_client_factory
        pool[role] = cls(**kwargs)
    return pool


def rebuild_helper(
    catalog: ModelCatalog,
    role: str,
    invoker: OllamaInvoker | None = None,
    skill_registry=None,
    vault_client_factory=None,
    ollama_url: str | None = None,
    router=None,
) -> Helper | None:
    """Construct a single helper after a catalog override change.

    Mirrors `build_helpers` per-role logic so the pool can be hot-swapped
    without rebuilding every helper.
    """
    cls = _HELPER_CLASSES.get(role)
    if cls is None:
        return None
    invoker = invoker or OllamaInvoker()
    h_entry = catalog.helper(role)
    m_entry = _resolve_model_entry(catalog, role, h_entry, router)
    try:
        schema = shape_for(h_entry.output_schema)
    except KeyError:
        schema = None
    merged_params = dict(m_entry.params)
    merged_params.update(dict(h_entry.params_override))
    kwargs = dict(
        model_id=m_entry.id,
        ollama_name=m_entry.ollama_name,
        prompt_name=h_entry.system_prompt_file,
        params=merged_params,
        invoker=invoker,
        timeout_s=h_entry.timeout_s,
        schema=schema,
    )
    if role == "skill_runner" and skill_registry is not None:
        kwargs["registry"] = skill_registry
    if role == "librarian":
        if vault_client_factory is not None:
            kwargs["vault_client_factory"] = vault_client_factory
        if ollama_url is not None:
            kwargs["ollama_url"] = ollama_url
    if role == "chat_recall" and vault_client_factory is not None:
        kwargs["vault_client_factory"] = vault_client_factory
    return cls(**kwargs)
