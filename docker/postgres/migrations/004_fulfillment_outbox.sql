-- Local simulation only: enqueue with the return, deliver outside the business transaction.
CREATE TABLE IF NOT EXISTS fulfillment_outbox (
    id UUID PRIMARY KEY,
    return_id UUID NOT NULL UNIQUE REFERENCES returns(id),
    user_id UUID NOT NULL REFERENCES users(id),
    order_id UUID NOT NULL REFERENCES orders(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    payload JSONB NOT NULL,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'PROCESSING', 'UNKNOWN', 'DELIVERED', 'NEEDS_REVIEW')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    claim_token UUID,
    lease_until TIMESTAMPTZ,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    supplier_receipt JSONB,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (status <> 'DELIVERED' OR supplier_receipt IS NOT NULL),
    CHECK (status <> 'PROCESSING' OR (claim_token IS NOT NULL AND lease_until IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_fulfillment_outbox_due
    ON fulfillment_outbox(next_attempt_at, created_at)
    WHERE status IN ('PENDING', 'UNKNOWN', 'PROCESSING');
CREATE INDEX IF NOT EXISTS idx_fulfillment_outbox_user ON fulfillment_outbox(user_id);
CREATE INDEX IF NOT EXISTS idx_fulfillment_outbox_order ON fulfillment_outbox(order_id);
