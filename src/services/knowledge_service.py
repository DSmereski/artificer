"""In-memory service for Knowledge Files and Tool Links.

Encapsulates storage behind a repository-style interface so the routes layer
depends on the abstraction, not the storage mechanism. Immutable-friendly:
mutating ops return fresh objects rather than editing caller-held instances.
"""

from __future__ import annotations

from typing import Optional

from src.models.knowledge import KnowledgeFile, LinkType, ToolLink


class KnowledgeFileNotFound(KeyError):
    """Raised when an operation targets a file_id that does not exist."""


class KnowledgeService:
    """Stores KnowledgeFiles and their ToolLinks in memory."""

    def __init__(self) -> None:
        self._files: dict[str, KnowledgeFile] = {}
        self._links: dict[str, list[ToolLink]] = {}

    def create_file(
        self,
        title: str,
        content_hash: str = "",
        description: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> KnowledgeFile:
        kf = KnowledgeFile(
            title=title,
            content_hash=content_hash,
            description=description,
            tags=list(tags) if tags else [],
        )
        self._files[kf.file_id] = kf
        self._links[kf.file_id] = []
        return kf

    def get_file(self, file_id: str) -> Optional[KnowledgeFile]:
        return self._files.get(file_id)

    def get_links(self, file_id: str) -> list[ToolLink]:
        return list(self._links.get(file_id, []))

    def link_tool(
        self,
        file_id: str,
        tool_id: str,
        link_type: LinkType = LinkType.REQUIRES,
    ) -> ToolLink:
        if file_id not in self._files:
            raise KnowledgeFileNotFound(file_id)
        link = ToolLink(
            tool_id=tool_id,
            knowledge_file_id=file_id,
            link_type=link_type,
        )
        self._links[file_id] = [*self._links[file_id], link]
        return link

    def unlink_tool(self, file_id: str, tool_id: str) -> bool:
        """Remove a tool link from a file.

        Returns True if a link was removed, False if no matching link existed.
        Raises KnowledgeFileNotFound when the file itself is unknown.
        """
        if file_id not in self._files:
            raise KnowledgeFileNotFound(file_id)
        existing = self._links[file_id]
        remaining = [link for link in existing if link.tool_id != tool_id]
        self._links[file_id] = remaining
        return len(remaining) != len(existing)
