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
-- Performance test (10,000,000 images)
----------------------------------------------------

-- Test data generated with generate_data.py 14 April 2026.
-- Data for 10,000,000 images generated and loaded successfully in 911.36 seconds.

select count(1) from bp_image;
-- 10000000

select count(1) from bp_image_extraction;
-- 7610704

----------------------------------------------------
-- Code search (GIN indexed)
----------------------------------------------------

-- 120 ms
-- 254 ms
-- EXPLAIN ANALYZE
-- "Bitmap Heap Scan on bp_image  (cost=954.27..4808.27 rows=1000 width=12) (actual time=2.177..25.430 rows=138 loops=1)"
-- "  Recheck Cond: (species @> '{outstanding}'::text[])"
-- "  Heap Blocks: exact=138"
-- "  ->  Bitmap Index Scan on idx_bp_image_species  (cost=0.00..954.02 rows=1000 width=0) (actual time=1.166..1.167 rows=138 loops=1)"
-- "        Index Cond: (species @> '{outstanding}'::text[])"
SELECT image_id FROM bp_image WHERE species @> ARRAY['outstanding']; -- 0.00001 selectivity

-- 162 ms
-- 209 ms
-- EXPLAIN ANALYZE
-- "Bitmap Heap Scan on bp_image  (cost=959.52..8528.51 rows=2000 width=12) (actual time=1.506..279.995 rows=1441 loops=1)"
-- "  Recheck Cond: (species @> '{excellent}'::text[])"
-- "  Heap Blocks: exact=1437"
-- "  ->  Bitmap Index Scan on idx_bp_image_species  (cost=0.00..959.02 rows=2000 width=0) (actual time=0.703..0.703 rows=1441 loops=1)"
-- "        Index Cond: (species @> '{excellent}'::text[])"
SELECT image_id FROM bp_image WHERE species @> ARRAY['excellent']; -- 0.0001 selectivity

-- 2.5 seconds
-- 1.6 seconds
-- EXPLAIN ANALYZE
-- "Bitmap Heap Scan on bp_image  (cost=1034.77..55296.18 rows=16334 width=12) (actual time=8.614..3490.886 rows=14949 loops=1)"
-- "  Recheck Cond: (species @> '{high}'::text[])"
-- "  Heap Blocks: exact=14651"
-- "  ->  Bitmap Index Scan on idx_bp_image_species  (cost=0.00..1030.69 rows=16334 width=0) (actual time=6.068..6.069 rows=14949 loops=1)"
-- "        Index Cond: (species @> '{high}'::text[])"
-- "Planning Time: 0.180 ms"
-- "Execution Time: 3497.892 ms"
SELECT image_id FROM bp_image WHERE species @> ARRAY['high']; -- 0.001 selectivity

-- 4.7 seconds
-- 4.0 seconds
-- EXPLAIN ANALYZE
-- "Gather  (cost=2727.81..338276.58 rows=148342 width=12) (actual time=55.401..5795.395 rows=148722 loops=1)"
-- "  Workers Planned: 2"
-- "  Workers Launched: 2"
-- "  ->  Parallel Bitmap Heap Scan on bp_image  (cost=1727.81..322442.38 rows=61809 width=12) (actual time=37.239..5752.382 rows=49574 loops=3)"
-- "        Recheck Cond: (species @> '{1}'::text[])"
-- "        Rows Removed by Index Recheck: 564504"
-- "        Heap Blocks: exact=18844 lossy=22311"
-- "        ->  Bitmap Index Scan on idx_bp_image_species  (cost=0.00..1690.73 rows=148342 width=0) (actual time=45.915..45.915 rows=148722 loops=1)"
-- "              Index Cond: (species @> '{1}'::text[])"
SELECT image_id FROM bp_image WHERE species @> ARRAY['1']; -- 0.01 selectivity

-- 2.7 seconds
-- 2.6 seconds
-- EXPLAIN ANALYZE
-- "Seq Scan on bp_image  (cost=0.00..500916.99 rows=724374 width=12) (actual time=0.359..2425.358 rows=725336 loops=1)"
-- "  Filter: (species @> '{5}'::text[])"
-- "  Rows Removed by Filter: 9274664"
SELECT image_id FROM bp_image WHERE species @> ARRAY['5']; -- 0.05 selectivity

-- 2.7 seconds
-- 2.5 seconds
-- EXPLAIN ANALYZE
-- "Seq Scan on bp_image  (cost=0.00..500916.99 rows=1399745 width=12) (actual time=0.353..2481.045 rows=1402930 loops=1)"
-- "  Filter: (species @> '{10}'::text[])"
-- "  Rows Removed by Filter: 8597070"
SELECT image_id FROM bp_image WHERE species @> ARRAY['10']; -- 0.1 selectivity

-- 4.2 seconds
-- 2.9 seconds
-- EXPLAIN ANALYZE
-- "Seq Scan on bp_image  (cost=0.00..500916.99 rows=7029060 width=12) (actual time=1.682..2294.446 rows=7021758 loops=1)"
-- "  Filter: (species @> '{poor}'::text[])"
-- "  Rows Removed by Filter: 2978242"
SELECT image_id FROM bp_image WHERE species @> ARRAY['poor']; -- 0.83889 selectivity

----------------------------------------------------
-- Text search (GIN indexed)
----------------------------------------------------

-- exact match (one word)
-- 15 seconds
-- EXPLAIN ANALYZE
-- "Gather  (cost=3439.27..1369664.43 rows=346686 width=266) (actual time=171.181..14146.316 rows=333410 loops=1)"
-- "  Workers Planned: 2"
-- "  Workers Launched: 2"
-- "  ->  Parallel Bitmap Heap Scan on bp_image  (cost=2439.27..1333995.83 rows=144452 width=266) (actual time=100.993..14052.369 rows=111137 loops=3)"
-- "        Recheck Cond: (to_tsvector('english'::regconfig, dataset_description) @@ '''microscopi'''::tsquery)"
-- "        Rows Removed by Index Recheck: 1380561"
-- "        Heap Blocks: exact=19536 lossy=55237"
-- "        ->  Bitmap Index Scan on idx_bp_image_dataset_description  (cost=0.00..2352.60 rows=346686 width=0) (actual time=163.384..163.389 rows=333410 loops=1)"
-- "              Index Cond: (to_tsvector('english'::regconfig, dataset_description) @@ '''microscopi'''::tsquery)"
SELECT image_id FROM bp_image WHERE to_tsvector('english', dataset_description) @@ to_tsquery('english','microscopy');

-- exact match (two words anywhere)
-- 28 seconds
-- EXPLAIN ANALYZE
-- "Bitmap Heap Scan on bp_image  (cost=1301.43..45477.08 rows=12018 width=266) (actual time=196.585..26865.863 rows=333410 loops=1)"
-- "  Recheck Cond: (to_tsvector('english'::regconfig, dataset_description) @@ '''microscopi'' & ''classif'''::tsquery)"
-- "  Rows Removed by Index Recheck: 4141684"
-- "  Heap Blocks: exact=58539 lossy=165136"
-- "  ->  Bitmap Index Scan on idx_bp_image_dataset_description  (cost=0.00..1298.43 rows=12018 width=0) (actual time=163.692..163.701 rows=333410 loops=1)"
-- "        Index Cond: (to_tsvector('english'::regconfig, dataset_description) @@ '''microscopi'' & ''classif'''::tsquery)"
SELECT image_id FROM bp_image WHERE to_tsvector('english', dataset_description) @@ to_tsquery('english','microscopy & classification');

-- exact match (two words next to each other)
-- 14.6 seconds
-- EXPLAIN ANALYZE
-- "Gather  (cost=2844.00..999271.38 rows=115366 width=266) (actual time=232.556..12408.283 rows=333410 loops=1)"
-- "  Workers Planned: 2"
-- "  Workers Launched: 2"
-- "  ->  Parallel Bitmap Heap Scan on bp_image  (cost=1844.00..986734.78 rows=48069 width=266) (actual time=218.023..12374.525 rows=111137 loops=3)"
-- "        Recheck Cond: (to_tsvector('english'::regconfig, dataset_description) @@ '''microscopi'' <-> ''imag'''::tsquery)"
-- "        Rows Removed by Index Recheck: 1380561"
-- "        Heap Blocks: exact=19576 lossy=55120"
-- "        ->  Bitmap Index Scan on idx_bp_image_dataset_description  (cost=0.00..1815.16 rows=115366 width=0) (actual time=224.641..224.642 rows=333410 loops=1)"
-- "              Index Cond: (to_tsvector('english'::regconfig, dataset_description) @@ '''microscopi'' <-> ''imag'''::tsquery)"
SELECT image_id FROM bp_image WHERE to_tsvector('english', dataset_description) @@ to_tsquery('english','microscopy <-> images');

-- ranked full‑text search
-- 13.6 seconds
-- EXPLAIN ANALYZE
-- "Gather Merge  (cost=1402119.69..1435827.51 rows=288904 width=270) (actual time=13222.028..13328.840 rows=333410 loops=1)"
-- "  Workers Planned: 2"
-- "  Workers Launched: 2"
-- "  ->  Sort  (cost=1401119.66..1401480.79 rows=144452 width=270) (actual time=13211.074..13237.726 rows=111137 loops=3)"
-- "        Sort Key: (ts_rank(to_tsvector('english'::regconfig, dataset_description), '''microscopi'''::tsquery)) DESC"
-- "        Sort Method: external merge  Disk: 33160kB"
-- "        Worker 0:  Sort Method: external merge  Disk: 33352kB"
-- "        Worker 1:  Sort Method: external merge  Disk: 33384kB"
-- "        ->  Parallel Bitmap Heap Scan on bp_image  (cost=2439.27..1370469.96 rows=144452 width=270) (actual time=58.128..13095.469 rows=111137 loops=3)"
-- "              Recheck Cond: (to_tsvector('english'::regconfig, dataset_description) @@ '''microscopi'''::tsquery)"
-- "              Rows Removed by Index Recheck: 1380561"
-- "              Heap Blocks: exact=19285 lossy=55079"
-- "              ->  Bitmap Index Scan on idx_bp_image_dataset_description  (cost=0.00..2352.60 rows=346686 width=0) (actual time=60.248..60.250 rows=333410 loops=1)"
-- "                    Index Cond: (to_tsvector('english'::regconfig, dataset_description) @@ '''microscopi'''::tsquery)"
SELECT image_id,
           ts_rank(to_tsvector('english', dataset_description),
               websearch_to_tsquery('english', 'microscopy')) AS rank
FROM bp_image
WHERE to_tsvector('english', dataset_description)
      @@ websearch_to_tsquery('english', 'microscopy')
ORDER BY rank DESC

----------------------------------------------------
-- Int range search (GIST indexed)
----------------------------------------------------

-- contains value (outstanding 0.001% selectivity)
-- 0.09 seconds
-- EXPLAIN ANALYZE
-- "Index Scan using idx_bp_image_age_at_extraction on bp_image_extraction  (cost=0.41..8.43 rows=1 width=12) (actual time=0.071..0.265 rows=96 loops=1)"
-- "  Index Cond: (age_at_extraction @> 1)"
-- "Planning Time: 0.095 ms"
-- "Execution Time: 0.284 ms"
SELECT image_id
FROM bp_image_extraction
WHERE age_at_extraction @> 1;

-- contains value (excellent 0.01% selectivity)
-- 0.09 seconds
-- EXPLAIN ANALYZE
-- "Bitmap Heap Scan on bp_image_extraction  (cost=446.72..30754.48 rows=12684 width=12) (actual time=1.113..2.511 rows=992 loops=1)"
-- "  Recheck Cond: (age_at_extraction @> 3)"
-- "  Heap Blocks: exact=981"
-- "  ->  Bitmap Index Scan on idx_bp_image_age_at_extraction  (cost=0.00..443.55 rows=12684 width=0) (actual time=0.917..0.918 rows=992 loops=1)"
-- "        Index Cond: (age_at_extraction @> 3)"
SELECT image_id
FROM bp_image_extraction
WHERE age_at_extraction @> 3;

-- contains value (high 0.1% selectivity)
-- 0.2 seconds
-- EXPLAIN ANALYZE
-- "Bitmap Heap Scan on bp_image_extraction  (cost=446.72..30754.48 rows=12684 width=12) (actual time=3.622..12.382 rows=10001 loops=1)"
-- "  Recheck Cond: (age_at_extraction @> 5)"
-- "  Heap Blocks: exact=9131"
-- "  ->  Bitmap Index Scan on idx_bp_image_age_at_extraction  (cost=0.00..443.55 rows=12684 width=0) (actual time=2.603..2.603 rows=10001 loops=1)"
-- "        Index Cond: (age_at_extraction @> 5)"
SELECT image_id
FROM bp_image_extraction
WHERE age_at_extraction @> 5;

-- contains value (1% selectivity)
-- 0.7 seconds
-- EXPLAIN ANALYZE
-- "Bitmap Heap Scan on bp_image_extraction  (cost=446.72..30754.48 rows=12684 width=12) (actual time=434.258..536.316 rows=99582 loops=1)"
"  Recheck Cond: (age_at_extraction @> 7)"
"  Heap Blocks: exact=46800"
"  ->  Bitmap Index Scan on idx_bp_image_age_at_extraction  (cost=0.00..443.55 rows=12684 width=0) (actual time=430.443..430.443 rows=99582 loops=1)"
"        Index Cond: (age_at_extraction @> 7)"
SELECT image_id
FROM bp_image_extraction
WHERE age_at_extraction @> 7;

-- contains value (5% selectivity)
-- 1.7 seconds
-- EXPLAIN ANALYZE
-- "Bitmap Heap Scan on bp_image_extraction  (cost=14728.38..75914.58 rows=418576 width=12) (actual time=2227.839..2370.609 rows=490296 loops=1)"
-- "  Recheck Cond: (age_at_extraction @> 9)"
-- "  Heap Blocks: exact=55948"
-- "  ->  Bitmap Index Scan on idx_bp_image_age_at_extraction  (cost=0.00..14623.74 rows=418576 width=0) (actual time=2222.182..2222.182 rows=490296 loops=1)"
-- "        Index Cond: (age_at_extraction @> 9)"
SELECT image_id
FROM bp_image_extraction
WHERE age_at_extraction @> 9;

-- contains value (10% selectivity)
-- 2.4 seconds
-- EXPLAIN ANALYZE
-- "Bitmap Heap Scan on bp_image_extraction  (cost=33469.07..101314.44 rows=951310 width=12) (actual time=3144.119..3315.473 rows=967071 loops=1)"
"  Recheck Cond: (age_at_extraction @> 11)"
"  Heap Blocks: exact=55954"
"  ->  Bitmap Index Scan on idx_bp_image_age_at_extraction  (cost=0.00..33231.24 rows=951310 width=0) (actual time=3138.541..3138.541 rows=967071 loops=1)"
"        Index Cond: (age_at_extraction @> 11)"
SELECT image_id
FROM bp_image_extraction
WHERE age_at_extraction @> 11;

-- contains value (poor 83% selectivity)
-- 1.4 seconds
-- EXPLAIN ANALYZE
-- "Seq Scan on bp_image_extraction  (cost=0.00..151084.96 rows=6055518 width=12) (actual time=0.108..536.937 rows=6042666 loops=1)"
"  Filter: (age_at_extraction @> 50)"
"  Rows Removed by Filter: 1568038"
"Planning Time: 0.139 ms"
"Execution Time: 647.094 ms"
SELECT image_id
FROM bp_image_extraction
WHERE age_at_extraction @> 50;

-- overlaps with range
-- outstanding 0.001% selectivity
-- < 1 seconds
SELECT image_id FROM bp_image_extraction WHERE age_at_extraction && int4range(1, 2);
-- excellent 0.01% selectivity
-- < 1 seconds
SELECT image_id FROM bp_image_extraction WHERE age_at_extraction && int4range(3, 4);
-- high 0.1% selectivity
-- < 1 seconds
SELECT image_id FROM bp_image_extraction WHERE age_at_extraction && int4range(5, 6);
-- 1% selectivity
-- 1.1 seconds
SELECT image_id FROM bp_image_extraction WHERE age_at_extraction && int4range(7, 8);
-- 5% selectivity
-- 3.3 seconds
SELECT image_id FROM bp_image_extraction WHERE age_at_extraction && int4range(9, 10);
-- 10% selectivity
-- 2.5 seconds
SELECT image_id FROM bp_image_extraction WHERE age_at_extraction && int4range(11, 12);
-- poor 83% selectivity
-- 1.5 seconds
SELECT image_id FROM bp_image_extraction WHERE age_at_extraction && int4range(13, 100);
