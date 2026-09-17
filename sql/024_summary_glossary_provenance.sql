CREATE TABLE IF NOT EXISTS process_summary_glossary_sources (
    summary_id UUID NOT NULL REFERENCES process_summaries(id) ON DELETE CASCADE,
    process_id UUID NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    version_id UUID NOT NULL REFERENCES process_versions(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('class', 'subject')),
    code TEXT NOT NULL CHECK (length(trim(code)) > 0),
    tpu_version TEXT NOT NULL CHECK (length(trim(tpu_version)) > 0),
    publisher TEXT NOT NULL CHECK (length(trim(publisher)) > 0),
    source TEXT NOT NULL CHECK (length(trim(source)) > 0),
    source_ref TEXT NOT NULL CHECK (length(trim(source_ref)) > 0),
    definition_sha256 TEXT NOT NULL CHECK (definition_sha256 ~ '^[0-9a-f]{64}$'),
    source_order INTEGER NOT NULL CHECK (source_order >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (summary_id, kind, code),
    UNIQUE (summary_id, source_order)
);

CREATE INDEX IF NOT EXISTS idx_summary_glossary_sources_process_version
    ON process_summary_glossary_sources (process_id, version_id, summary_id);

CREATE OR REPLACE FUNCTION sync_process_summary_glossary_sources()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    DELETE FROM process_summary_glossary_sources
    WHERE summary_id = NEW.id;

    INSERT INTO process_summary_glossary_sources (
        summary_id,
        process_id,
        version_id,
        kind,
        code,
        tpu_version,
        publisher,
        source,
        source_ref,
        definition_sha256,
        source_order
    )
    SELECT
        NEW.id,
        NEW.process_id,
        NEW.version_id,
        item.value->>'kind',
        item.value->>'code',
        item.value->>'tpu_version',
        item.value->>'publisher',
        item.value->>'source',
        item.value->>'source_ref',
        item.value->>'definition_sha256',
        (item.ordinality - 1)::integer
    FROM processes p
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(p.header->'tpu_glossary') = 'array'
            THEN p.header->'tpu_glossary'
            ELSE '[]'::jsonb
        END
    ) WITH ORDINALITY AS item(value, ordinality)
    WHERE p.id = NEW.process_id
      AND p.current_version_id = NEW.version_id
      AND p.secrecy_level = 0
      AND item.value->>'kind' IN ('class', 'subject')
      AND length(trim(COALESCE(item.value->>'code', ''))) > 0
      AND length(trim(COALESCE(item.value->>'tpu_version', ''))) > 0
      AND length(trim(COALESCE(item.value->>'publisher', ''))) > 0
      AND length(trim(COALESCE(item.value->>'source', ''))) > 0
      AND length(trim(COALESCE(item.value->>'source_ref', ''))) > 0
      AND COALESCE(item.value->>'definition_sha256', '') ~ '^[0-9a-f]{64}$';

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_process_summary_glossary_sources ON process_summaries;
CREATE TRIGGER trg_process_summary_glossary_sources
AFTER INSERT OR UPDATE OF markdown, validation, model, prompt_version
ON process_summaries
FOR EACH ROW
EXECUTE FUNCTION sync_process_summary_glossary_sources();
