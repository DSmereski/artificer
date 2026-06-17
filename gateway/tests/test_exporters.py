"""Tests for gateway.exporters (doc_builder, slide_builder) and the
`generate_doc` / `generate_deck` action verbs."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from gateway.action_executor import ActionExecutor
from gateway.exporters.doc_builder import build_docx
from gateway.exporters.slide_builder import build_pptx


# ---------------------------------------------------------------- doc_builder


def test_build_docx_writes_file(tmp_path: Path) -> None:
    out = build_docx(
        "Title", "# Heading\n\nbody **bold** text.\n\n- one\n- two\n",
        tmp_path / "out.docx",
    )
    assert out.exists() and out.stat().st_size > 0
    # docx is a zip — verify it parses.
    with zipfile.ZipFile(out) as z:
        assert "word/document.xml" in z.namelist()
        body = z.read("word/document.xml").decode("utf-8")
        assert "Heading" in body
        assert "bold" in body
        assert "one" in body and "two" in body


def test_build_docx_blank_body_ok(tmp_path: Path) -> None:
    out = build_docx("Empty", "", tmp_path / "empty.docx")
    assert out.exists()


def test_build_docx_creates_parent_dir(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c.docx"
    out = build_docx("T", "hi", nested)
    assert out.exists()


# ---------------------------------------------------------------- slide_builder


def test_build_pptx_writes_file(tmp_path: Path) -> None:
    sections = [
        {"heading": "Topic A", "bullets": ["alpha", "beta"], "notes": "spk"},
        {"heading": "Topic B", "bullets": "single bullet"},
        {"heading": "Empty", "bullets": []},
    ]
    out = build_pptx("Deck", sections, tmp_path / "deck.pptx", subtitle="sub")
    assert out.exists() and out.stat().st_size > 0
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        # Title slide + 3 content slides = 4 slides.
        slide_files = [n for n in names if n.startswith("ppt/slides/slide")]
        assert len(slide_files) == 4


def test_build_pptx_skips_non_dict_section(tmp_path: Path) -> None:
    out = build_pptx(
        "Deck",
        [{"heading": "Real", "bullets": ["x"]}, "not-a-dict", None],
        tmp_path / "d.pptx",
    )
    assert out.exists()


# ---------------------------------------------------------------- action verbs


@pytest.mark.asyncio
async def test_generate_doc_verb_happy(tmp_path: Path) -> None:
    ex = ActionExecutor(state_dir=tmp_path)
    receipts = await ex.execute_all([{
        "verb": "generate_doc",
        "payload": {"title": "Brief", "body_md": "# H\n\nbody"},
    }])
    assert len(receipts) == 1
    r = receipts[0]
    assert r.ok, r.detail
    assert r.payload["path"].endswith(".docx")
    assert Path(r.payload["path"]).exists()
    assert r.payload["size_bytes"] > 0


@pytest.mark.asyncio
async def test_generate_doc_missing_title(tmp_path: Path) -> None:
    ex = ActionExecutor(state_dir=tmp_path)
    receipts = await ex.execute_all([{
        "verb": "generate_doc",
        "payload": {"body_md": "x"},
    }])
    assert receipts[0].ok is False
    assert "title" in receipts[0].detail


@pytest.mark.asyncio
async def test_generate_doc_missing_body(tmp_path: Path) -> None:
    ex = ActionExecutor(state_dir=tmp_path)
    receipts = await ex.execute_all([{
        "verb": "generate_doc",
        "payload": {"title": "x"},
    }])
    assert receipts[0].ok is False
    assert "body_md" in receipts[0].detail


@pytest.mark.asyncio
async def test_generate_doc_no_state_dir() -> None:
    ex = ActionExecutor()  # state_dir=None
    receipts = await ex.execute_all([{
        "verb": "generate_doc",
        "payload": {"title": "x", "body_md": "y"},
    }])
    assert receipts[0].ok is False
    assert "state_dir" in receipts[0].detail


@pytest.mark.asyncio
async def test_generate_deck_verb_happy(tmp_path: Path) -> None:
    ex = ActionExecutor(state_dir=tmp_path)
    receipts = await ex.execute_all([{
        "verb": "generate_deck",
        "payload": {
            "title": "Phase 1",
            "sections": [
                {"heading": "Goals", "bullets": ["a", "b"]},
                {"heading": "Status", "bullets": ["done"]},
            ],
        },
    }])
    r = receipts[0]
    assert r.ok, r.detail
    assert r.payload["path"].endswith(".pptx")
    assert r.payload["section_count"] == 2
    assert Path(r.payload["path"]).exists()


@pytest.mark.asyncio
async def test_generate_deck_empty_sections(tmp_path: Path) -> None:
    ex = ActionExecutor(state_dir=tmp_path)
    receipts = await ex.execute_all([{
        "verb": "generate_deck",
        "payload": {"title": "x", "sections": []},
    }])
    assert receipts[0].ok is False


@pytest.mark.asyncio
async def test_generate_deck_slug_sanitised(tmp_path: Path) -> None:
    ex = ActionExecutor(state_dir=tmp_path)
    receipts = await ex.execute_all([{
        "verb": "generate_deck",
        "payload": {
            "title": "x",
            "slug": "../../etc/passwd",
            "sections": [{"heading": "s", "bullets": ["a"]}],
        },
    }])
    r = receipts[0]
    assert r.ok
    # Slug must not allow path traversal; final path stays under state_dir.
    p = Path(r.payload["path"]).resolve()
    assert str(tmp_path.resolve()) in str(p)
    assert ".." not in r.payload["slug"]
