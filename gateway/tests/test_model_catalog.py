"""Tests for the M2.1 model catalog."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

import gateway.model_catalog as model_catalog
from gateway.model_catalog import (
    ModelCatalog, _installed_from_api, _ollama_base_url, _ollama_name_present,
    _parse_ollama_list, load_catalog,
)


_VALID_YAML = dedent("""\
    models:
      - id: qwen-7b
        ollama_name: qwen2.5:7b
        family: qwen2.5
        gpu_vram_mb: 5500
        cpu_ram_mb: 6500
        cpu_fallback: true
        speciality: planning
        use_for: [planner, critic]
        params: {temperature: 0.3}
      - id: nomic-embed
        ollama_name: nomic-embed-text
        family: embedding
        gpu_vram_mb: 800
        cpu_ram_mb: null
        cpu_fallback: false
        speciality: embeddings
        use_for: [vault_search]
        params: {}

    helpers:
      - role: planner
        model: qwen-7b
        system_prompt_file: prompts/planner.md
        output_schema: HelperPlan
        timeout_s: 30
      - role: critic
        model: qwen-7b
        system_prompt_file: prompts/critic.md
        output_schema: CriticReport
        timeout_s: 30
""")


def _write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "catalog.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_load_catalog_basic(tmp_path: Path) -> None:
    cat = load_catalog(_write_yaml(tmp_path, _VALID_YAML))
    assert cat.model_ids == ["qwen-7b", "nomic-embed"]
    assert sorted(cat.helper_roles) == ["critic", "planner"]
    m = cat.model("qwen-7b")
    assert m.ollama_name == "qwen2.5:7b"
    assert m.cpu_fallback is True
    assert m.params == {"temperature": 0.3}
    n = cat.model("nomic-embed")
    assert n.cpu_ram_mb is None
    assert n.cpu_fallback is False


def test_helper_lookup(tmp_path: Path) -> None:
    cat = load_catalog(_write_yaml(tmp_path, _VALID_YAML))
    h = cat.helper("planner")
    assert h.model == "qwen-7b"
    assert h.timeout_s == 30


def test_models_for_role(tmp_path: Path) -> None:
    cat = load_catalog(_write_yaml(tmp_path, _VALID_YAML))
    assert [m.id for m in cat.models_for_role("planner")] == ["qwen-7b"]
    assert cat.models_for_role("nonexistent") == []


def test_unknown_model_id_raises(tmp_path: Path) -> None:
    cat = load_catalog(_write_yaml(tmp_path, _VALID_YAML))
    with pytest.raises(KeyError):
        cat.model("does-not-exist")


def test_helper_referencing_missing_model_rejected(tmp_path: Path) -> None:
    bad = dedent("""\
        models:
          - id: only-model
            ollama_name: a
            family: f
            gpu_vram_mb: 1
            cpu_ram_mb: 1
            cpu_fallback: true
            speciality: ''
            use_for: []
            params: {}
        helpers:
          - role: orphan
            model: nonexistent-model
            system_prompt_file: prompts/x.md
            output_schema: X
            timeout_s: 10
    """)
    with pytest.raises(ValueError, match="unknown model"):
        load_catalog(_write_yaml(tmp_path, bad))


def test_duplicate_model_id_rejected(tmp_path: Path) -> None:
    body = dedent("""\
        models:
          - id: x
            ollama_name: a
            family: f
            gpu_vram_mb: 1
            cpu_ram_mb: 1
            cpu_fallback: true
            speciality: ''
            use_for: []
            params: {}
          - id: x
            ollama_name: b
            family: f
            gpu_vram_mb: 1
            cpu_ram_mb: 1
            cpu_fallback: true
            speciality: ''
            use_for: []
            params: {}
        helpers: []
    """)
    with pytest.raises(ValueError, match="duplicate model id"):
        load_catalog(_write_yaml(tmp_path, body))


def test_render_for_hive_prompt(tmp_path: Path) -> None:
    cat = load_catalog(_write_yaml(tmp_path, _VALID_YAML))
    out = cat.render_for_hive_prompt()
    assert "planner" in out
    assert "qwen2.5:7b" in out
    assert len(out) <= 2000


def test_render_marks_unavailable(tmp_path: Path) -> None:
    cat = load_catalog(_write_yaml(tmp_path, _VALID_YAML))
    cat._available["qwen-7b"] = False
    out = cat.render_for_hive_prompt()
    assert "UNAVAILABLE" in out


def test_parse_ollama_list_skips_header() -> None:
    raw = (
        "NAME                  ID            SIZE      MODIFIED\n"
        "qwen2.5:7b            abc123def     4.7 GB    2 days ago\n"
        "qwen2.5-coder:7b      def456abc     4.6 GB    1 week ago\n"
    )
    names = _parse_ollama_list(raw)
    assert names == {"qwen2.5:7b", "qwen2.5-coder:7b"}


def test_ollama_base_url_defaults_and_scheme(monkeypatch) -> None:
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert _ollama_base_url() == "http://127.0.0.1:11434"
    monkeypatch.setenv("OLLAMA_HOST", "10.0.0.5:11434")
    assert _ollama_base_url() == "http://10.0.0.5:11434"
    monkeypatch.setenv("OLLAMA_HOST", "https://gpu.box:443/")
    assert _ollama_base_url() == "https://gpu.box:443"


def test_installed_from_api_parses_tags(monkeypatch) -> None:
    import io
    import json as _json

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()
            return False

    payload = {"models": [{"name": "qwen2.5:7b"}, {"name": "nomic-embed-text"}]}

    def _fake_urlopen(req, timeout=0):
        return _Resp(_json.dumps(payload).encode())

    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", _fake_urlopen)
    assert _installed_from_api("http://x") == {"qwen2.5:7b", "nomic-embed-text"}


def test_ollama_name_present_exact() -> None:
    assert _ollama_name_present({"qwen2.5:7b"}, "qwen2.5:7b") is True
    assert _ollama_name_present({"qwen2.5:7b"}, "qwen2.5:14b") is False


def test_ollama_name_present_latest_tag() -> None:
    assert _ollama_name_present({"foo:latest"}, "foo") is True


def test_ollama_name_present_variant_suffix() -> None:
    # Quantization variants count as the same model.
    assert _ollama_name_present(
        {"qwen2.5:7b-instruct-q4_K_M"}, "qwen2.5:7b",
    ) is True


def test_helper_with_explicit_candidates(tmp_path: Path) -> None:
    body = dedent("""\
        models:
          - id: planner-qwen
            ollama_name: planner-qwen
            family: qwen2.5
            gpu_vram_mb: 9500
            cpu_ram_mb: 11000
            cpu_fallback: true
            speciality: x
            use_for: [chat_recall]
            params: {}
          - id: claude-haiku-4-5-20251001
            cloud_provider: anthropic
            cloud_model_name: claude-haiku-4-5-20251001
            cost_per_1k_tokens_input: 0.0008
            cost_per_1k_tokens_output: 0.004
            speciality: cloud
            use_for: [chat_recall]
            params: {}
        helpers:
          - role: chat_recall
            model: planner-qwen
            candidates: [planner-qwen, claude-haiku-4-5-20251001]
            system_prompt_file: prompts/x.md
            output_schema: X
            timeout_s: 10
    """)
    cat = load_catalog(_write_yaml(tmp_path, body))
    cands = cat.candidates_for_role("chat_recall")
    assert [m.id for m in cands] == ["planner-qwen", "claude-haiku-4-5-20251001"]

    cloud_m = cat.model("claude-haiku-4-5-20251001")
    assert cloud_m.cloud_provider == "anthropic"
    assert cloud_m.cost_per_1k_tokens_input == 0.0008
    assert cloud_m.ollama_name is None


def test_helper_without_candidates_falls_back_to_single_model(tmp_path: Path) -> None:
    cat = load_catalog(_write_yaml(tmp_path, _VALID_YAML))
    cands = cat.candidates_for_role("planner")
    assert [m.id for m in cands] == ["qwen-7b"]


def test_unknown_candidate_id_rejected(tmp_path: Path) -> None:
    body = dedent("""\
        models:
          - id: only-one
            ollama_name: a
            family: f
            gpu_vram_mb: 1
            cpu_ram_mb: 1
            cpu_fallback: true
            speciality: x
            use_for: []
            params: {}
        helpers:
          - role: planner
            model: only-one
            candidates: [only-one, ghost-model]
            system_prompt_file: prompts/x.md
            output_schema: X
            timeout_s: 10
    """)
    with pytest.raises(ValueError, match="unknown candidate"):
        load_catalog(_write_yaml(tmp_path, body))


def test_helper_override_swaps_model(tmp_path: Path) -> None:
    body = dedent("""\
        models:
          - id: a-model
            ollama_name: a
            family: f
            gpu_vram_mb: 1
            cpu_ram_mb: 1
            cpu_fallback: true
            speciality: x
            use_for: [planner]
            params: {}
          - id: b-model
            ollama_name: b
            family: f
            gpu_vram_mb: 1
            cpu_ram_mb: 1
            cpu_fallback: true
            speciality: y
            use_for: [planner]
            params: {}
        helpers:
          - role: planner
            model: a-model
            system_prompt_file: prompts/x.md
            output_schema: X
            timeout_s: 10
    """)
    cat = load_catalog(_write_yaml(tmp_path, body))
    assert cat.helper("planner").model == "a-model"
    assert cat.get_override("planner") is None
    cat.set_override("planner", "b-model")
    h = cat.helper("planner")
    assert h.model == "b-model"
    assert h.candidates[0] == "b-model"
    assert cat.get_override("planner") == "b-model"
    cat.clear_override("planner")
    assert cat.helper("planner").model == "a-model"
    assert cat.get_override("planner") is None


def test_helper_override_validates_role_and_model(tmp_path: Path) -> None:
    cat = load_catalog(_write_yaml(tmp_path, _VALID_YAML))
    with pytest.raises(KeyError):
        cat.set_override("ghost-role", "qwen-7b")
    with pytest.raises(ValueError):
        cat.set_override("planner", "ghost-model")


def test_helper_override_persists_to_disk(tmp_path: Path) -> None:
    overrides_path = tmp_path / "helper_overrides.json"
    body = dedent("""\
        models:
          - id: a-model
            ollama_name: a
            family: f
            gpu_vram_mb: 1
            cpu_ram_mb: 1
            cpu_fallback: true
            speciality: x
            use_for: [planner]
            params: {}
          - id: b-model
            ollama_name: b
            family: f
            gpu_vram_mb: 1
            cpu_ram_mb: 1
            cpu_fallback: true
            speciality: y
            use_for: [planner]
            params: {}
        helpers:
          - role: planner
            model: a-model
            system_prompt_file: prompts/x.md
            output_schema: X
            timeout_s: 10
    """)
    cat1 = load_catalog(_write_yaml(tmp_path, body))
    cat1.attach_overrides_file(overrides_path)
    cat1.set_override("planner", "b-model")
    assert overrides_path.is_file()

    cat2 = load_catalog(tmp_path / "catalog.yaml")
    cat2.attach_overrides_file(overrides_path)
    assert cat2.get_override("planner") == "b-model"
    assert cat2.helper("planner").model == "b-model"


def test_helper_override_drops_stale_entries_on_load(tmp_path: Path) -> None:
    import json as _json
    overrides_path = tmp_path / "helper_overrides.json"
    overrides_path.write_text(
        _json.dumps({"planner": "ghost-model", "ghost-role": "qwen-7b"}),
        encoding="utf-8",
    )
    cat = load_catalog(_write_yaml(tmp_path, _VALID_YAML))
    cat.attach_overrides_file(overrides_path)
    assert cat.get_override("planner") is None
    assert cat.get_override("ghost-role") is None


def test_model_must_have_ollama_or_cloud(tmp_path: Path) -> None:
    body = dedent("""\
        models:
          - id: ghost
            family: f
            speciality: x
            use_for: []
            params: {}
        helpers: []
    """)
    with pytest.raises(ValueError, match="ollama_name|cloud"):
        load_catalog(_write_yaml(tmp_path, body))


# ---- #476 Phase A: gemma3-ablit CPU researcher registration --------------

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_REAL_CATALOG = _PROJECT_ROOT / "config" / "model_catalog.yaml"


def test_gemma3_ablit_4b_registered_cpu_only() -> None:
    cat = load_catalog(_REAL_CATALOG)
    m = cat.model("gemma3-4b")
    assert m.ollama_name == "gemma3:4b"
    assert m.gpu_vram_mb == 0
    assert m.cpu_fallback is True
    assert m.cpu_ram_mb is not None and m.cpu_ram_mb >= 3000
    assert "researcher" in m.use_for


def test_researcher_candidates_include_gemma3_ablit() -> None:
    cat = load_catalog(_REAL_CATALOG)
    cands = [m.id for m in cat.candidates_for_role("researcher")]
    assert "planner-qwen" in cands
    assert "gemma3-4b" in cands
