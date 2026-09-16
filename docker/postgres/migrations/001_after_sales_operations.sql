-- Incremental M3 migration. Refuse existing duplicate returns; never delete data.
LOCK TABLE returns IN SHARE ROW EXCLUSIVE MODE;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM returns WHERE order_id IS NOT NULL GROUP BY order_id HAVING count(*) > 1) THEN
        RAISE EXCEPTION 'Duplicate returns exist; review them before applying migration 001';
    END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS ux_returns_one_per_order ON returns(order_id);

CREATE TABLE IF NOT EXISTS after_sales_operations (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    operation_id UUID NOT NULL,
    order_id UUID NOT NULL REFERENCES orders(id),
    action TEXT NOT NULL DEFAULT 'initiate_return' CHECK (action = 'initiate_return'),
    payload_hash TEXT NOT NULL,
    request_payload JSONB NOT NULL,
    policy_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'READY' CHECK (status IN (
        'READY', 'NEEDS_REVIEW', 'AWAITING_APPROVAL', 'SUCCEEDED', 'REJECTED', 'RETRYABLE_FAILURE'
    )),
    result JSONB,
    approval_id UUID REFERENCES tool_approval_requests(id) ON DELETE SET NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (user_id, operation_id)
);
CREATE INDEX IF NOT EXISTS idx_after_sales_order ON after_sales_operations(order_id);

CREATE TABLE IF NOT EXISTS after_sales_operation_events (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL,
    operation_id UUID NOT NULL,
    request_id UUID NOT NULL,
    attempt INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (user_id, operation_id) REFERENCES after_sales_operations(user_id, operation_id) ON DELETE CASCADE
);
