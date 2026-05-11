CREATE TABLE bp_image (
    image_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    dataset_image_cnt INT NOT NULL,
    dataset_short_name TEXT,
    dataset_title TEXT,
    dataset_description TEXT,
    dataset_short_name_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', dataset_short_name)) STORED,
    dataset_title_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', dataset_title)) STORED,
    dataset_description_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', dataset_description)) STORED,
    blocks JSONB, -- block related search fields
    stains JSONB, -- staining related search fields
    search_sync BOOLEAN NOT NULL DEFAULT false,
    search_sync_date timestamptz
);

----------------------------------------------------
-- Indexes
----------------------------------------------------

CREATE INDEX idx_bp_image_dataset_short_name_tsv ON bp_image USING GIN (dataset_short_name_tsv);
CREATE INDEX idx_bp_image_dataset_title_tsv ON bp_image USING GIN (dataset_title_tsv);
CREATE INDEX idx_bp_image_dataset_description_tsv ON bp_image USING GIN (dataset_description_tsv);
CREATE INDEX idx_bp_image_blocks ON bp_image USING GIN (blocks jsonb_path_ops); -- Only supports @> (containment) queries.
CREATE INDEX idx_bp_image_stains ON bp_image USING GIN (stains jsonb_path_ops); -- Only supports @> (containment) queries.

CREATE INDEX idx_bp_image_search_sync ON bp_image (search_sync);
