CREATE TABLE bp_sample (
    image_id TEXT,
    dataset_id TEXT,
    dataset_description TEXT,                -- text
    species TEXT[],                          -- array of string codes
    anatomical_site TEXT[],                  -- array of string codes
    sex TEXT[],                              -- array of string codes
    fixation_type TEXT[],                    -- array of string codes
    block_preparation TEXT[],                -- array of string codes
    specimen_type TEXT[]                     -- array of string codes
);

CREATE TABLE bp_sample_extraction (
    image_id TEXT,
    age_at_extraction INT[]                  -- array of ints
);

----------------------------------------------------
-- Indexes
----------------------------------------------------

CREATE INDEX idx_bp_sample_dataset_description ON bp_sample USING GIN (to_tsvector('english', dataset_description));
CREATE INDEX idx_bp_sample_species ON bp_sample USING GIN (species);
CREATE INDEX idx_bp_sample_anatomical_site ON bp_sample USING GIN (anatomical_site);
CREATE INDEX idx_bp_sample_sex ON bp_sample USING GIN (sex);
CREATE INDEX idx_bp_sample_fixation_type ON bp_sample USING GIN (fixation_type);
CREATE INDEX idx_bp_sample_block_preparation ON bp_sample USING GIN (block_preparation);
CREATE INDEX idx_bp_sample_specimen_type ON bp_sample USING GIN (specimen_type);
CREATE INDEX idx_bp_sample_age_at_extraction ON bp_sample_extraction USING GIN (age_at_extraction);

----------------------------------------------------
-- Search examples
----------------------------------------------------

-- GIN indexed array string (code) column search examples

-- contains code
SELECT * FROM bp_sample WHERE species @> ARRAY['code'];

-- GIN indexed text column search examples

-- exact match (one word)
SELECT * FROM bp_sample WHERE to_tsvector('english', dataset_description) @@ to_tsquery('english','melanoma');

-- exact match (two words anywhere)
SELECT * FROM bp_sample WHERE to_tsvector('english', dataset_description) @@ to_tsquery('english','liver & tumor');

-- exact match (two words next to each other)
SELECT * FROM bp_sample WHERE to_tsvector('english', dataset_description) @@ to_tsquery('english','breast <-> cancer'); -- proximity search

-- ranked full‑text search
SELECT *,
       ts_rank(to_tsvector('english', dataset_description),
               websearch_to_tsquery('english', 'melanoma glaucoma')) AS rank
FROM bp_sample
WHERE to_tsvector('english', dataset_description)
      @@ websearch_to_tsquery('english', 'melanoma glaucoma')
ORDER BY rank DESC

-- GIN indexed array int column (age) search examples

-- overlaps with range
SELECT * FROM bp_sample_extraction WHERE age_at_extraction && ARRAY[10,20];

-- contains value
SELECT * FROM bp_sample_extraction WHERE age_at_extraction @> ARRAY[12];
