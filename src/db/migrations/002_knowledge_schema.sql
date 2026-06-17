-- Migration 002: Knowledge Schema
-- Creates tables backing the KnowledgeFile and ToolLink data models
-- (see src/models/knowledge.py). Idempotent: safe to run multiple times.

-- Tools table is referenced by tool_links.tool_id. It is created here with
-- IF NOT EXISTS so this migration is self-contained and the foreign key below
-- resolves even when run against a fresh database. A migration that owns the
-- full tools schema can extend it; this only guarantees the referenced column
-- exists.
CREATE TABLE IF NOT EXISTS tools (
    id TEXT PRIMARY KEY,
    name TEXT
);

CREATE TABLE IF NOT EXISTS knowledge_files (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
    description TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tool_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id TEXT NOT NULL,
    knowledge_file_id TEXT NOT NULL,
    link_type TEXT NOT NULL DEFAULT 'requires',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tool_id) REFERENCES tools(id),
    FOREIGN KEY (knowledge_file_id) REFERENCES knowledge_files(id),
    UNIQUE(tool_id, knowledge_file_id)
);

CREATE INDEX IF NOT EXISTS idx_tool_links_tool_id
    ON tool_links(tool_id);

CREATE INDEX IF NOT EXISTS idx_tool_links_knowledge_file_id
    ON tool_links(knowledge_file_id);

CREATE INDEX IF NOT EXISTS idx_knowledge_files_content_hash
    ON knowledge_files(content_hash);
