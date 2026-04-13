CREATE TABLE bp_image (
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

CREATE TABLE bp_image_extraction (
    image_id TEXT,
    age_at_extraction int4range              -- int range
);

----------------------------------------------------
-- Indexes
----------------------------------------------------

CREATE INDEX idx_bp_image_dataset_description ON bp_image USING GIN (to_tsvector('english', dataset_description));
CREATE INDEX idx_bp_image_species ON bp_image USING GIN (species);
CREATE INDEX idx_bp_image_anatomical_site ON bp_image USING GIN (anatomical_site);
CREATE INDEX idx_bp_image_sex ON bp_image USING GIN (sex);
CREATE INDEX idx_bp_image_fixation_type ON bp_image USING GIN (fixation_type);
CREATE INDEX idx_bp_image_block_preparation ON bp_image USING GIN (block_preparation);
CREATE INDEX idx_bp_image_specimen_type ON bp_image USING GIN (specimen_type);
CREATE INDEX idx_bp_image_age_at_extraction ON bp_image_extraction USING GIST (age_at_extraction);

----------------------------------------------------
-- Check execution plans
----------------------------------------------------

EXPLAIN ANALYZE
SELECT ...

-- No index used:
-- Seq Scan on bp_image


----------------------------------------------------
-- Search examples
----------------------------------------------------

-- GIN indexed array string (code) column search examples

-- contains code
-- < 2 seconds for 1 million images
-- ~ 12 seconds for 10 million images
SELECT * FROM bp_image
WHERE species @> ARRAY['high'];

-- GIN indexed text column search examples

-- exact match (one word)
-- < 1 second for 1 million images
-- ~ 15 second for 10 million images
SELECT * FROM bp_image WHERE to_tsvector('english', dataset_description) @@ to_tsquery('english','microscopy');

-- exact match (two words anywhere)
-- < 1 second for 1 million images
-- ~ 28 second for 10 million images
SELECT * FROM bp_image WHERE to_tsvector('english', dataset_description) @@ to_tsquery('english','microscopy & classification');

-- exact match (two words next to each other)
-- < 1 second for 1 million images
-- ~ 15 second for 10 million images
SELECT * FROM bp_image WHERE to_tsvector('english', dataset_description) @@ to_tsquery('english','microscopy <-> images');

-- ranked full‑text search
-- < 1 second for 1 million images
-- ~ 16 second for 10 million images
SELECT *,
           ts_rank(to_tsvector('english', dataset_description),
               websearch_to_tsquery('english', 'microscopy')) AS rank
FROM bp_image
WHERE to_tsvector('english', dataset_description)
      @@ websearch_to_tsquery('english', 'microscopy')
ORDER BY rank DESC

-- GIST indexed array int column (age) search examples

-- overlaps with range
-- ~2 seconds for 1 million images
-- ~21 seconds for 10 million images
SELECT * FROM bp_image_extraction
WHERE age_at_extraction && int4range(5, 6)

-- contains value
-- < 2 seconds for 1 million images (full table scan with GIN index)
-- ~11 seconds for 10 million images (full table scan with GIN index)
SELECT *
FROM bp_image_extraction
WHERE age_at_extraction @> 50;