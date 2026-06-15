CREATE TABLE bp_snomed (
    concept_id     TEXT        PRIMARY KEY,
    preferred_term TEXT        NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_bp_snomed_updated_at ON bp_snomed (updated_at);

CREATE TABLE bp_image (
    image_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    dataset_image_cnt INT NOT NULL,
    dataset_short_name TEXT,
    dataset_title TEXT,
    dataset_description TEXT,
    blocks JSONB, -- block related search fields
    stains JSONB, -- staining related search fields
    dataset_files_date timestamptz, --  newest file modification date in the dataset
    opensearch_sync_date timestamptz
);

CREATE INDEX idx_bp_image_dataset_id ON bp_image (dataset_id);

-- Partial index to keep it smaller.
CREATE INDEX idx_bp_image_opensearch_sync ON bp_image (image_id) WHERE opensearch_sync_date IS NULL;
