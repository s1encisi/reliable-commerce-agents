-- 保留原计划与回执；旧计划执行前会要求补充验收契约。
ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS requirements JSONB NOT NULL DEFAULT '{}';
ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS constraint_confirmation JSONB;
