ALTER TABLE judit_deliveries
    ADD COLUMN IF NOT EXISTS request_completed BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS judit_deliveries_completed_request_idx
ON judit_deliveries (request_id, received_at DESC)
WHERE request_completed = TRUE;
