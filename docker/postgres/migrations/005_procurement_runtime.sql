-- Procurement runtime is additive; legacy query tasks keep their existing contract.
ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS task_type TEXT NOT NULL DEFAULT 'query';
ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS execution_mode TEXT NOT NULL DEFAULT 'offline';
ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS draft_spec JSONB;
ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS spec_revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS plan_revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS plan_hash TEXT;
ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS previous_plan JSONB;
ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS options JSONB NOT NULL DEFAULT '[]';
ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS counters JSONB NOT NULL DEFAULT '{}';
ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS stop_reason TEXT;
ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS state_seq BIGINT NOT NULL DEFAULT 0;
ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS procurement_generation BIGINT NOT NULL DEFAULT 0;
ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS lease_token UUID;
ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS lease_owner TEXT;
ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;
ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ;
ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS run_deadline TIMESTAMPTZ;
ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS latest_checkpoint_id TEXT;
ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS checkpoint_plan_hash TEXT;
ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS checkpoint_layer INTEGER NOT NULL DEFAULT -1;

CREATE INDEX IF NOT EXISTS idx_procurement_claim ON agent_tasks(status, lease_expires_at, created_at)
    WHERE task_type = 'procurement';

CREATE TABLE IF NOT EXISTS procurement_steps (
    task_id UUID NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
    spec_revision INTEGER NOT NULL,
    plan_revision INTEGER NOT NULL,
    step_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    definition JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt INTEGER NOT NULL DEFAULT 0,
    attempt_id UUID,
    input_hash TEXT,
    outcome JSONB,
    valid_until TIMESTAMPTZ,
    reuse_invalidated BOOLEAN NOT NULL DEFAULT FALSE,
    reused_from JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (task_id, plan_revision, step_id)
);
CREATE INDEX IF NOT EXISTS idx_procurement_reuse
    ON procurement_steps(task_id,input_hash,valid_until) WHERE status='succeeded' AND reuse_invalidated=FALSE;

CREATE TABLE IF NOT EXISTS procurement_attempts (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
    spec_revision INTEGER NOT NULL,
    plan_revision INTEGER NOT NULL,
    step_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    generation BIGINT NOT NULL,
    lease_token UUID NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    finished_at TIMESTAMPTZ,
    accepted BOOLEAN,
    outcome JSONB,
    UNIQUE (task_id,plan_revision,step_id,attempt)
);

ALTER TABLE procurement_attempts ADD COLUMN IF NOT EXISTS late_outcome JSONB;

CREATE TABLE IF NOT EXISTS procurement_artifacts (
    artifact_id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
    attempt_id UUID NOT NULL REFERENCES procurement_attempts(id),
    plan_revision INTEGER NOT NULL,
    step_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    source TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    valid_until TIMESTAMPTZ NOT NULL,
    data JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS procurement_events (
    task_id UUID NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
    seq BIGINT NOT NULL,
    event TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (task_id,seq)
);
