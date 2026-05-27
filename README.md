# SD Search API

## Description

The SD Search API enables search across different datasets. At first,
it will support Bigpicture image search.

## Development

### Postgres

Launch a Postgres container by running:

`docker compose --profile dev up --build`

## OpenSearch

Follow these instructions to launch an OpenSearch container:
https://oofnivek.medium.com/macos-running-opensearch-f0ccf3d55af7

Pull containers and create s network:

```
docker pull opensearchproject/opensearch:3.1.0
docker pull opensearchproject/opensearch-dashboards:3.1.0
docker network create my-opensearch-network
```

Start the OpenSearch container and check that it is running:

```
docker run -d \
  -p 9200:9200 -p 9600:9600 \
  -e "discovery.type=single-node" \
  -e "OPENSEARCH_JAVA_OPTS=-Xms512m -Xmx512m" \
  -e "plugins.security.disabled=true" \
  -e "DISABLE_INSTALL_DEMO_CONFIG=true" \
  --name my-opensearch-node \
  --network my-opensearch-network \
  opensearchproject/opensearch:3.1.0

curl -X GET "http://localhost:9200/"
```

Start the OpenSearch container dashboard and check that it is running:

```
docker run -d \
  -p 5601:5601 \
  -e "OPENSEARCH_HOSTS=[\"http://my-opensearch-node:9200\"]" \
  -e "DISABLE_SECURITY_DASHBOARDS_PLUGIN=true" \
  --name my-opensearch-dashboards \
  --network my-opensearch-network \
  opensearchproject/opensearch-dashboards:3.1.0

http://localhost:5601
```

Create bp-image-index index:

```
curl -X PUT "http://localhost:9200/bp-image-index" \
  -H "Content-Type: application/json" \
  -d @search_api/opensearch/bigpicture/bp-image-index.json
```

See the bp-image-index settings and mappings:

```
curl -X GET "http://localhost:9200/bp-image-index/_settings?pretty"
curl -X GET "http://localhost:9200/bp-image-index/_mapping?pretty"
```

Delete bp-image-index index:

```
curl -X DELETE "http://localhost:9200/bp-image-index"
```

### Search

### All images

```
curl -X POST "http://localhost:9200/bp-image-index/_search" -H "Content-Type: application/json" -d'
{
  "query": {
    "match_all": {}
  }
}'
```

### Text search

```
curl -X POST "http://localhost:9200/bp-image-index/_search" -H "Content-Type: application/json" -d'
{
  "query": {
    "match": {
      "dataset_description": "natural variation"
    }
  }
}'
```

### Code search

Selectivity: 0.001%

```
curl -X POST "http://localhost:9200/bp-image-index/_search" -H "Content-Type: application/json" -d'
{
  "query": {
    "term": {
      "species": "outstanding"
    }
  }
}'
```

Selectivity: 0.01%

```
curl -X POST "http://localhost:9200/bp-image-index/_search" -H "Content-Type: application/json" -d'
{
  "query": {
    "term": {
      "species": "excellent"
    }
  }
}'
```

Selectivity: 0.1%

```
curl -X POST "http://localhost:9200/bp-image-index/_search" -H "Content-Type: application/json" -d'
{
  "query": {
    "term": {
      "species": "high"
    }
  }
}'
```

Selectivity: 1%

```
curl -X POST "http://localhost:9200/bp-image-index/_search" -H "Content-Type: application/json" -d'
{
  "query": {
    "term": {
      "species": "1"
    }
  }
}'
```

Selectivity: 5%

```
curl -X POST "http://localhost:9200/bp-image-index/_search" -H "Content-Type: application/json" -d'
{
  "query": {
    "term": {
      "species": "5"
    }
  }
}'
```

Selectivity: 10%

```
curl -X POST "http://localhost:9200/bp-image-index/_search" -H "Content-Type: application/json" -d'
{
  "query": {
    "term": {
      "species": "10"
    }
  }
}'
```

Selectivity: 83.9%

```
curl -X POST "http://localhost:9200/bp-image-index/_search" -H "Content-Type: application/json" -d'
{
  "query": {
    "term": {
      "species": "poor"
    }
  }
}'
```

### Range query

```
curl -X POST "http://localhost:9200/bp-image-index/_search" -H "Content-Type: application/json" -d'
{
  "query": {
    "range": {
      "age_at_extraction": {
        "gte": 30,
        "lte": 50
      }
    }
  }
}
'
```

### Combined query

```
curl -X POST "http://localhost:9200/bp-image-index/_search" -H "Content-Type: application/json" -d'
{
  "query": {
    "bool": {
      "must": [
        { "term": { "species": "excellent" } },
        { "term": { "sex": "Female" } }
      ],
      "filter": [
        {
          "range": {
            "age_at_extraction": {
              "gte": 20,
              "lte": 40
            }
          }
        }
      ],
      "should": [
        {
          "match": {
            "dataset_description": "natural variation"
          }
        }
      ]
    }
  }
}
'
```

## Ollama

The AI search endpoint uses a local [Ollama](https://ollama.com) model. Install and start it before running the API:

```bash
brew install ollama
ollama pull qwen2.5:14b
ollama serve
```

## AI-powered search

The `/ai/query` endpoint accepts a natural language query. Claude translates it into
Beacon V2 filters using the MCP server and returns structured results.

Requires `ANTHROPIC_API_KEY` to be set in the environment.

```bash
curl -X POST "http://localhost:8000/ai/query" \
  -H "Content-Type: application/json" \
  -d '{"query": "find female human lung tissue images with immunostaining, age 40-60"}'
```

Example response:

```json
{
  "interpretation": "Searching for images of female human subjects with lung anatomical site, immunostaining method, and age at extraction between 40 and 60 years.",
  "filters": [
    {"id": "sex", "value": "Female"},
    {"id": "anatomical_site", "value": "lung"},
    {"id": "staining_method", "value": "immunostaining"},
    {"id": "age_at_extraction", "value": "40-60"}
  ],
  "result_count": 142,
  "datasets": [
    {
      "dataset_id": "ds-001",
      "dataset_title": "Lung Cancer Cohort",
      "matching_image_count": 142,
      "total_image_count": 5000
    }
  ]
}
```

Note: SNOMED CT ontology resolution (Snowstorm) is not yet integrated. Ontology fields
are searched using the term or code passed directly.

## Bigpicture OpenSearch performance tests

Data for OpenSearch performance tests has been generated and
loaded with tests/performance/bigpicture/generate_data.py.

| Number of images | Operation             | Time (seconds) |
|------------------|-----------------------|----------------|
| 1,000,000        | Loading to PostgreSQL | 349.10         |
| 1,000,000        | Syncing to OpenSearch | 282.26         |

The performance tests have been done using Locust and reports
are available in tests/performance/bigpicture/opensearch directory.

## Bigpicture Postgres performance tests

Data for performance tests has been generated and loaded with
tests/performance/bigpicture/generate_data.py.

### 10,000,000 images

- Test data generated with generate_data.py 14 April 2026.
- Data for 10,000,000 images generated and loaded successfully in 911.36 seconds.
- Images synced to OpenSearch in 8526.20 seconds (2 hours 22 minutes).

```
select count(1) from bp_image;
-- 10000000

select count(1) from bp_image_extraction;
-- 7610704
```

#### Code search (GIN indexed)

```
-- 120 ms without LIMIT
-- 254 ms without LIMIT
-- 102 ms with LIMIT 100
-- EXPLAIN ANALYZE
-- "Bitmap Heap Scan on bp_image  (cost=954.27..4808.27 rows=1000 width=12) (actual time=2.177..25.430 rows=138 loops=1)"
-- "  Recheck Cond: (species @> '{outstanding}'::text[])"
-- "  Heap Blocks: exact=138"
-- "  ->  Bitmap Index Scan on idx_bp_image_species  (cost=0.00..954.02 rows=1000 width=0) (actual time=1.166..1.167 rows=138 loops=1)"
-- "        Index Cond: (species @> '{outstanding}'::text[])"
SELECT image_id FROM bp_image WHERE species @> ARRAY['outstanding'] -- 0.00001 selectivity
LIMIT 100;

-- 162 ms without LIMIT
-- 209 ms without LIMIT
-- 81 ms with LIMIT 100
-- EXPLAIN ANALYZE
-- "Bitmap Heap Scan on bp_image  (cost=959.52..8528.51 rows=2000 width=12) (actual time=1.506..279.995 rows=1441 loops=1)"
-- "  Recheck Cond: (species @> '{excellent}'::text[])"
-- "  Heap Blocks: exact=1437"
-- "  ->  Bitmap Index Scan on idx_bp_image_species  (cost=0.00..959.02 rows=2000 width=0) (actual time=0.703..0.703 rows=1441 loops=1)"
-- "        Index Cond: (species @> '{excellent}'::text[])"
SELECT image_id FROM bp_image WHERE species @> ARRAY['excellent'] -- 0.0001 selectivity
LIMIT 100;

-- 2.5 seconds without LIMIT
-- 1.6 seconds without LIMIT
-- 73ms with LIMIT 100
-- EXPLAIN ANALYZE
-- "Bitmap Heap Scan on bp_image  (cost=1034.77..55296.18 rows=16334 width=12) (actual time=8.614..3490.886 rows=14949 loops=1)"
-- "  Recheck Cond: (species @> '{high}'::text[])"
-- "  Heap Blocks: exact=14651"
-- "  ->  Bitmap Index Scan on idx_bp_image_species  (cost=0.00..1030.69 rows=16334 width=0) (actual time=6.068..6.069 rows=14949 loops=1)"
-- "        Index Cond: (species @> '{high}'::text[])"
-- "Planning Time: 0.180 ms"
-- "Execution Time: 3497.892 ms"
SELECT image_id FROM bp_image WHERE species @> ARRAY['high'] -- 0.001 selectivity
LIMIT 100;

-- 4.7 seconds without LIMIT
-- 4.0 seconds without LIMIT
-- 195ms with LIMIT 100
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
SELECT image_id FROM bp_image WHERE species @> ARRAY['1'] -- 0.01 selectivity
LIMIT 100;
-- 2.7 seconds without LIMIT
-- 2.6 seconds without LIMIT
-- 127ms with LIMIT 100
-- EXPLAIN ANALYZE
-- "Seq Scan on bp_image  (cost=0.00..500916.99 rows=724374 width=12) (actual time=0.359..2425.358 rows=725336 loops=1)"
-- "  Filter: (species @> '{5}'::text[])"
-- "  Rows Removed by Filter: 9274664"
SELECT image_id FROM bp_image WHERE species @> ARRAY['5'] -- 0.05 selectivity
LIMIT 100;

-- 2.7 seconds without LIMIT
-- 2.5 seconds without LIMIT
-- 77ms with LIMIT 100
-- EXPLAIN ANALYZE
-- "Seq Scan on bp_image  (cost=0.00..500916.99 rows=1399745 width=12) (actual time=0.353..2481.045 rows=1402930 loops=1)"
-- "  Filter: (species @> '{10}'::text[])"
-- "  Rows Removed by Filter: 8597070"
SELECT image_id FROM bp_image WHERE species @> ARRAY['10'] -- 0.1 selectivity
LIMIT 100;

-- 4.2 seconds without LIMIT
-- 2.9 seconds without LIMIT
-- 85ms with LIMIT 100
-- EXPLAIN ANALYZE
-- "Seq Scan on bp_image  (cost=0.00..500916.99 rows=7029060 width=12) (actual time=1.682..2294.446 rows=7021758 loops=1)"
-- "  Filter: (species @> '{poor}'::text[])"
-- "  Rows Removed by Filter: 2978242"
SELECT image_id FROM bp_image WHERE species @> ARRAY['poor'] -- 0.83889 selectivity
LIMIT 100;
```

#### Text search (GIN indexed)

```
-- exact match (one word)
-- 9.0 seconds WITHOUT LIMIT
-- 100 ms WITH LIMIT 100
-- EXPLAIN ANALYZE
-- "Gather  (cost=3389.92..573886.56 rows=337287 width=12) (actual time=132.394..8708.845 rows=334152 loops=1)"
-- "  Workers Planned: 2"
-- "  Workers Launched: 2"
-- "  ->  Parallel Bitmap Heap Scan on bp_image  (cost=2389.92..539157.86 rows=140536 width=12) (actual time=117.974..8654.697 rows=111384 loops=3)"
-- "        Recheck Cond: (dataset_description_tsv @@ '''microscopi'''::tsquery)"
-- "        Rows Removed by Index Recheck: 1151791"
-- "        Heap Blocks: exact=17719 lossy=65915"
-- "        ->  Bitmap Index Scan on bp_image_dataset_description_tsv  (cost=0.00..2305.60 rows=337287 width=0) (actual time=125.883..125.883 rows=334152 loops=1)"
-- "              Index Cond: (dataset_description_tsv @@ '''microscopi'''::tsquery)"
SELECT image_id
FROM bp_image
WHERE dataset_description_tsv @@ websearch_to_tsquery('english', 'microscopy')
LIMIT 100;

-- exact match (two words anywhere)
SELECT image_id FROM bp_image WHERE dataset_description_tsv @@ websearch_to_tsquery('english','microscopy & classification')
LIMIT 100;

-- exact match (two words next to each other)
SELECT image_id FROM bp_image WHERE dataset_description_tsv @@ websearch_to_tsquery('english','microscopy <-> images')
LIMIT 100;

-- ranked full‑text search with full ranking
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
           ts_rank(dataset_description_tsv,
               websearch_to_tsquery('english', 'microscopy')) AS rank
FROM bp_image
WHERE dataset_description_tsv
      @@ websearch_to_tsquery('english', 'microscopy')
ORDER BY rank DESC
LIMIT 100;

-- ranked full‑text search with first N (5000) ranking
-- 0.1 seconds
-- EXPLAIN ANALYZE
-- "Limit  (cost=9937.74..9937.99 rows=100 width=16) (actual time=49.919..49.925 rows=100 loops=1)"
-- "  ->  Sort  (cost=9937.74..9950.24 rows=5000 width=16) (actual time=49.919..49.921 rows=100 loops=1)"
-- "        Sort Key: (ts_rank(candidates.dataset_description_tsv, '''microscopi'''::tsquery)) DESC"
-- "        Sort Method: top-N heapsort  Memory: 28kB"
-- "        ->  Subquery Scan on candidates  (cost=0.00..9746.64 rows=5000 width=16) (actual time=0.034..49.412 rows=5000 loops=1)"
-- "              ->  Limit  (cost=0.00..9734.14 rows=5000 width=134) (actual time=0.032..48.679 rows=5000 loops=1)"
-- "                    ->  Seq Scan on bp_image  (cost=0.00..656639.81 rows=337287 width=134) (actual time=0.031..48.383 rows=5000 loops=1)"
-- "                          Filter: (dataset_description_tsv @@ '''microscopi'''::tsquery)"
-- "                          Rows Removed by Filter: 142613"
WITH candidates AS (
    SELECT image_id, dataset_description_tsv
    FROM bp_image
    WHERE dataset_description_tsv @@ websearch_to_tsquery('english', 'microscopy')
    LIMIT 5000
)
SELECT image_id,
       ts_rank(dataset_description_tsv,
               websearch_to_tsquery('english', 'microscopy')) AS rank
FROM candidates
ORDER BY rank DESC
LIMIT 100;
```

#### Int range search (GIST indexed)

```
-- contains value (outstanding 0.001% selectivity)
-- 0.09 seconds without LIMIT
-- EXPLAIN ANALYZE
-- "Index Scan using idx_bp_image_age_at_extraction on bp_image_extraction  (cost=0.41..8.43 rows=1 width=12) (actual time=0.071..0.265 rows=96 loops=1)"
-- "  Index Cond: (age_at_extraction @> 1)"
-- "Planning Time: 0.095 ms"
-- "Execution Time: 0.284 ms"
SELECT image_id
FROM bp_image_extraction
WHERE age_at_extraction @> 1;

-- contains value (excellent 0.01% selectivity)
-- 0.09 seconds without LIMIT
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
-- 0.2 seconds without LIMIT
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
-- 0.7 seconds without LIMIT
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
-- 1.7 seconds without LIMIT
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
-- 2.4 seconds without LIMIT
-- 93 ms with LIMIT 100
-- EXPLAIN ANALYZE
-- "Bitmap Heap Scan on bp_image_extraction  (cost=33469.07..101314.44 rows=951310 width=12) (actual time=3144.119..3315.473 rows=967071 loops=1)"
"  Recheck Cond: (age_at_extraction @> 11)"
"  Heap Blocks: exact=55954"
"  ->  Bitmap Index Scan on idx_bp_image_age_at_extraction  (cost=0.00..33231.24 rows=951310 width=0) (actual time=3138.541..3138.541 rows=967071 loops=1)"
"        Index Cond: (age_at_extraction @> 11)"
SELECT image_id
FROM bp_image_extraction
WHERE age_at_extraction @> 11
LIMIT 100;

-- contains value (poor 83% selectivity)
-- 1.4 seconds without LIMIT
-- 109 ms with LIMIT 100
-- EXPLAIN ANALYZE
-- "Seq Scan on bp_image_extraction  (cost=0.00..151084.96 rows=6055518 width=12) (actual time=0.108..536.937 rows=6042666 loops=1)"
"  Filter: (age_at_extraction @> 50)"
"  Rows Removed by Filter: 1568038"
SELECT image_id
FROM bp_image_extraction
WHERE age_at_extraction @> 50
LIMIT 100;

-- overlaps with range
-- outstanding 0.001% selectivity
-- < 1 seconds WITHOUT LIMIT
-- 67ms WITH LIMIT 100
SELECT image_id FROM bp_image_extraction WHERE age_at_extraction && int4range(1, 2) LIMIT 100;
-- excellent 0.01% selectivity
-- < 1 seconds WITHOUT LIMIT
-- 89ms WITH LIMIT 100
SELECT image_id FROM bp_image_extraction WHERE age_at_extraction && int4range(3, 4) LIMIT 100;
-- high 0.1% selectivity
-- < 1 seconds WITHOUT LIMIT
-- 69ms WITH LIMIT 100
SELECT image_id FROM bp_image_extraction WHERE age_at_extraction && int4range(5, 6) LIMIT 100;
-- 1% selectivity
-- 1.1 seconds WITHOUT LIMIT
-- 82ms WITH LIMIT 100
SELECT image_id FROM bp_image_extraction WHERE age_at_extraction && int4range(7, 8) LIMIT 100;
-- 5% selectivity
-- 3.3 seconds WITHOUT LIMIT
-- 72ms WITH LIMIT 100
SELECT image_id FROM bp_image_extraction WHERE age_at_extraction && int4range(9, 10) LIMIT 100;
-- 10% selectivity
-- 2.5 seconds WITHOUT LIMIT
-- 114ms WITH LIMIT 100
SELECT image_id FROM bp_image_extraction WHERE age_at_extraction && int4range(11, 12) LIMIT 100;
-- poor 83% selectivity
-- 1.5 seconds WITHOUT LIMIT
-- 61ms WITH LIMIT 100
SELECT image_id FROM bp_image_extraction WHERE age_at_extraction && int4range(13, 100) LIMIT 100;
```

#### Staining search (GIN indexed JSON)


```
SELECT image_id
FROM bp_image
WHERE stains @> '[
  {
    "staining_method": "immunostaining",
    "staining_target": "test_target"
  }
]'
LIMIT 100;
```

