"""Tests for Knowledge File and Tool Link data models."""

from src.models.knowledge import KnowledgeFile, ToolLink


def test_imports_knowledge_file_and_tool_link():
    assert KnowledgeFile is not None
    assert ToolLink is not None


def test_knowledge_file_has_required_attributes():
    kf = KnowledgeFile(file_id="kf-1", title="Notes", content_hash="abc123")
    assert kf.file_id == "kf-1"
    assert kf.title == "Notes"
    assert kf.content_hash == "abc123"


def test_knowledge_file_generates_file_id_by_default():
    kf = KnowledgeFile()
    assert isinstance(kf.file_id, str)
    assert kf.file_id


def test_tool_link_has_required_attributes():
    tl = ToolLink(tool_id="t-1", knowledge_file_id="kf-1", link_type="requires")
    assert tl.tool_id == "t-1"
    assert tl.knowledge_file_id == "kf-1"
    assert tl.link_type == "requires"


def test_knowledge_module_exports_both_classes():
    """AC: knowledge.py exports class KnowledgeFile and class ToolLink."""
    import inspect

    from src.models import knowledge

    assert inspect.isclass(knowledge.KnowledgeFile)
    assert inspect.isclass(knowledge.ToolLink)


def test_knowledge_file_exposes_all_required_fields():
    """AC: KnowledgeFile has attributes file_id, title, content_hash."""
    kf = KnowledgeFile(file_id="kf-9", title="Spec", content_hash="deadbeef")
    for attr in ("file_id", "title", "content_hash"):
        assert hasattr(kf, attr)
    assert kf.content_hash == "deadbeef"


def test_tool_link_exposes_all_required_fields():
    """AC: ToolLink has attributes tool_id, knowledge_file_id, link_type."""
    tl = ToolLink(tool_id="t-9", knowledge_file_id="kf-9", link_type="extends")
    for attr in ("tool_id", "knowledge_file_id", "link_type"):
        assert hasattr(tl, attr)


def test_tool_link_default_link_type_is_set():
    """Link status/type usable without explicit args (API-ready instantiation)."""
    tl = ToolLink()
    assert tl.link_type is not None
    assert tl.tool_id == ""
    assert tl.knowledge_file_id == ""
