-- 增量迁移：不删除或重建任何业务数据。
CREATE TABLE IF NOT EXISTS agent_tasks (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id),
    goal TEXT NOT NULL,
    constraints JSONB NOT NULL DEFAULT '[]',
    plan JSONB,
    results JSONB NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    revision INTEGER NOT NULL DEFAULT 0,
    planning_attempts INTEGER NOT NULL DEFAULT 0,
    active_step TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (planning_attempts BETWEEN 0 AND 2)
);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_user_updated ON agent_tasks(user_id, updated_at DESC);

ALTER TABLE agent_memories ADD COLUMN IF NOT EXISTS source_kind TEXT NOT NULL DEFAULT 'legacy_unverified';
ALTER TABLE agent_memories ADD COLUMN IF NOT EXISTS source_ref TEXT;
ALTER TABLE agent_memories ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ;


CREATE TABLE IF NOT EXISTS conversation_contexts (
    conversation_id UUID PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id),
    snapshot JSONB NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conversation_contexts_user ON conversation_contexts(user_id);

ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS receipt_history JSONB NOT NULL DEFAULT '[]';
