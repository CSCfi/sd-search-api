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
    species TEXT[],                          -- array of string codes
    anatomical_site TEXT[],                  -- array of string codes
    sex TEXT[],                              -- array of string codes
    fixation_type TEXT[],                    -- array of string codes
    block_preparation TEXT[],                -- array of string codes
    specimen_type TEXT[],                    -- array of string codes
    search_sync BOOLEAN NOT NULL DEFAULT false,
    search_sync_date timestamptz
);

CREATE TABLE bp_image_extraction (
    image_id TEXT NOT NULL,
    age_at_extraction int4range              -- int range
);

----------------------------------------------------
-- Indexes
----------------------------------------------------

CREATE INDEX idx_bp_image_dataset_short_name_tsv ON bp_image USING GIN (dataset_short_name_tsv);
CREATE INDEX idx_bp_image_dataset_title_tsv ON bp_image USING GIN (dataset_title_tsv);
CREATE INDEX idx_bp_image_dataset_description_tsv ON bp_image USING GIN (dataset_description_tsv);
CREATE INDEX idx_bp_image_species ON bp_image USING GIN (species);
CREATE INDEX idx_bp_image_anatomical_site ON bp_image USING GIN (anatomical_site);
CREATE INDEX idx_bp_image_sex ON bp_image USING GIN (sex);
CREATE INDEX idx_bp_image_fixation_type ON bp_image USING GIN (fixation_type);
CREATE INDEX idx_bp_image_block_preparation ON bp_image USING GIN (block_preparation);
CREATE INDEX idx_bp_image_specimen_type ON bp_image USING GIN (specimen_type);
CREATE INDEX idx_bp_image_search_sync ON bp_image (search_sync);
CREATE INDEX idx_bp_image_extraction_image_id ON bp_image_extraction (image_id);
CREATE INDEX idx_bp_image_age_at_extraction ON bp_image_extraction USING GIST (age_at_extraction);
