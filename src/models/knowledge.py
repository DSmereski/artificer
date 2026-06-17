"""Data models for Knowledge Files and Tool Links."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class LinkType(str, Enum):
    """Types of links between tools and knowledge files."""

    REQUIRES = "requires"
    OPTIONAL = "optional"
    EXTENDS = "extends"


@dataclass
class KnowledgeFile:
    """Represents a knowledge file with metadata."""

    file_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    content_hash: str = ""
    description: Optional[str] = None
    tags: list[str] = field(default_factory=list)


@dataclass
class ToolLink:
    """Represents a link between a tool and a knowledge file."""

    tool_id: str = ""
    knowledge_file_id: str = ""
    link_type: LinkType = LinkType.REQUIRES
    status: str = "active"
