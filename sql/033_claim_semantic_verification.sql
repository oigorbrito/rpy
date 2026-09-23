ALTER TABLE process_summary_claims
    ADD COLUMN IF NOT EXISTS verification_status TEXT NOT NULL
        DEFAULT 'not_evaluated'
        CHECK (
            verification_status IN (
                'supported', 'contradicted', 'insufficient', 'not_evaluated'
            )
        ),
    ADD COLUMN IF NOT EXISTS verification_reason TEXT;

ALTER TABLE process_summary_claim_sources
    ADD COLUMN IF NOT EXISTS verification_status TEXT NOT NULL
        DEFAULT 'not_evaluated'
        CHECK (
            verification_status IN (
                'supported', 'contradicted', 'insufficient', 'not_evaluated'
            )
        ),
    ADD COLUMN IF NOT EXISTS verification_reason TEXT,
    ADD COLUMN IF NOT EXISTS evidence_excerpt TEXT,
    ADD COLUMN IF NOT EXISTS evidence_excerpt_sha256 TEXT
        CHECK (
            evidence_excerpt_sha256 IS NULL
            OR evidence_excerpt_sha256 ~ '^[0-9a-f]{64}$'
        ),
    ADD COLUMN IF NOT EXISTS page_start INTEGER
        CHECK (page_start IS NULL OR page_start >= 1),
    ADD COLUMN IF NOT EXISTS page_end INTEGER,
    ADD COLUMN IF NOT EXISTS char_start INTEGER
        CHECK (char_start IS NULL OR char_start >= 0),
    ADD COLUMN IF NOT EXISTS char_end INTEGER;

ALTER TABLE process_summary_claim_sources
    DROP CONSTRAINT IF EXISTS process_summary_claim_sources_page_range_check,
    ADD CONSTRAINT process_summary_claim_sources_page_range_check
        CHECK (
            (page_start IS NULL AND page_end IS NULL)
            OR (
                page_start IS NOT NULL
                AND page_end IS NOT NULL
                AND page_end >= page_start
            )
        ),
    DROP CONSTRAINT IF EXISTS process_summary_claim_sources_char_range_check,
    ADD CONSTRAINT process_summary_claim_sources_char_range_check
        CHECK (
            (char_start IS NULL AND char_end IS NULL)
            OR (
                char_start IS NOT NULL
                AND char_end IS NOT NULL
                AND char_end >= char_start
            )
        );

CREATE INDEX IF NOT EXISTS idx_process_summary_claims_verification
    ON process_summary_claims (summary_id, verification_status);

CREATE INDEX IF NOT EXISTS idx_process_summary_claim_sources_verification
    ON process_summary_claim_sources (summary_id, verification_status);
