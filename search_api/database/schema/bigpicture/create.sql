CREATE TABLE bp_image (
    image_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    dataset_image_cnt INT NOT NULL,
    dataset_short_name TEXT,
    dataset_title TEXT,
    dataset_description TEXT,
    blocks JSONB, -- block related search fields
    stains JSONB, -- staining related search fields
    search_sync BOOLEAN NOT NULL DEFAULT false,
    search_sync_date timestamptz
);

CREATE INDEX idx_bp_image_dataset_id ON bp_image (dataset_id);

-- Partial index to keep it smaller.
CREATE INDEX idx_bp_image_search_sync ON bp_image (image_id) WHERE search_sync = false;
