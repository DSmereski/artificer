"""REST API routes for Knowledge File management.

Exposes:
  POST   /api/v1/knowledge                      create a KnowledgeFile
  GET    /api/v1/knowledge/{file_id}            retrieve a file with its links
  POST   /api/v1/knowledge/{file_id}/link-tool  link a tool to a file
  PUT    /api/v1/knowledge/{file_id}/link-tool  (alias of POST, per spec)

Request/response serialization mirrors the data models in
``src.models.knowledge``.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from src.models.knowledge import LinkType, ToolLink
from src.services.knowledge_service import KnowledgeFileNotFound, KnowledgeService

# Single in-process service instance backing the routes.
_service = KnowledgeService()


def get_service() -> KnowledgeService:
    """Accessor so tests can reach (and reset) the backing store."""
    return _service


class CreateKnowledgeRequest(BaseModel):
    title: str = Field(..., min_length=1)
    content_hash: str = ""
    description: Optional[str] = None
    tags: list[str] = Field(default_factory=list)


class LinkToolRequest(BaseModel):
    tool_id: str = Field(..., min_length=1)
    link_type: LinkType = LinkType.REQUIRES


class UnlinkToolRequest(BaseModel):
    tool_id: str = Field(..., min_length=1)


class ToolLinkResponse(BaseModel):
    tool_id: str
    knowledge_file_id: str
    link_type: LinkType
    status: str

    @classmethod
    def from_model(cls, link: ToolLink) -> "ToolLinkResponse":
        return cls(
            tool_id=link.tool_id,
            knowledge_file_id=link.knowledge_file_id,
            link_type=link.link_type,
            status=link.status,
        )


class KnowledgeFileResponse(BaseModel):
    file_id: str
    title: str
    content_hash: str
    description: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    links: list[ToolLinkResponse] = Field(default_factory=list)


router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=KnowledgeFileResponse)
def create_knowledge(payload: CreateKnowledgeRequest) -> KnowledgeFileResponse:
    kf = get_service().create_file(
        title=payload.title,
        content_hash=payload.content_hash,
        description=payload.description,
        tags=payload.tags,
    )
    return KnowledgeFileResponse(
        file_id=kf.file_id,
        title=kf.title,
        content_hash=kf.content_hash,
        description=kf.description,
        tags=kf.tags,
        links=[],
    )


@router.get("/{file_id}", response_model=KnowledgeFileResponse)
def get_knowledge(file_id: str) -> KnowledgeFileResponse:
    service = get_service()
    kf = service.get_file(file_id)
    if kf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="knowledge file not found")
    links = [ToolLinkResponse.from_model(link) for link in service.get_links(file_id)]
    return KnowledgeFileResponse(
        file_id=kf.file_id,
        title=kf.title,
        content_hash=kf.content_hash,
        description=kf.description,
        tags=kf.tags,
        links=links,
    )


@router.api_route(
    "/{file_id}/link-tool",
    methods=["POST", "PUT"],
    status_code=status.HTTP_201_CREATED,
    response_model=ToolLinkResponse,
)
def link_tool(file_id: str, payload: LinkToolRequest) -> ToolLinkResponse:
    try:
        link = get_service().link_tool(
            file_id=file_id,
            tool_id=payload.tool_id,
            link_type=payload.link_type,
        )
    except KnowledgeFileNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="knowledge file not found")
    return ToolLinkResponse.from_model(link)


@router.post("/{file_id}/unlink-tool", status_code=status.HTTP_204_NO_CONTENT)
def unlink_tool(file_id: str, payload: UnlinkToolRequest) -> None:
    try:
        removed = get_service().unlink_tool(file_id=file_id, tool_id=payload.tool_id)
    except KnowledgeFileNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="knowledge file not found")
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tool link not found")
    return None
