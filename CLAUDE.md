# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run all checks (ruff format+lint, mypy, pytest)
tox

# Individual checks
tox -e ruff     # format and lint
tox -e mypy     # type check
tox -e pytest   # unit tests with coverage (tests/unit/ only)

# Run a single test
.venv/bin/pytest tests/unit/path/to/test_file.py::test_name -x

# Run the API server (dev)
uvicorn search_api.main:app --reload
# or via the installed script
sd_search_api

# Start Postgres + OpenSearch (required for data loading/sync)
docker compose --profile dev up --build
```

Dependencies are managed with `uv`. The virtualenv is at `.venv/`.

## Architecture

The API is a **FastAPI** app (`search_api/main.py`) that implements the **Beacon V2** protocol for Bigpicture image search. It has two separate backends:

### Dual-backend data flow

1. **PostgreSQL** (`search_api/database/repository.py`, `search_api/bigpicture/service.py`) — primary store. Images are loaded via `load_fields()` into the `bp_image` table (JSONB columns for `blocks`/`stains`). A `search_sync` flag tracks which rows need syncing to OpenSearch.

2. **OpenSearch** (`search_api/services/search.py`) — search index (`bp-image-index`). `sync_fields()` reads unsynced rows from Postgres, converts ISO-8601 `age_at_extraction` ranges to days via `_convert_blocks_for_opensearch`, bulk-indexes in batches of 1000, then marks rows synced.

### XML ingestion pipeline (`search_api/bigpicture/process.py`)

`extract_fields(root, fs, use_aliases) → Iterator[BigpictureFields]` walks a directory tree and reads four XML files per dataset:

| File | Extracts |
|---|---|
| `METADATA/dataset.xml` | dataset ID, title, description |
| `METADATA/image.xml` | image IDs, slide mappings |
| `METADATA/sample.xml` | biological beings, cases, specimens, blocks |
| `METADATA/staining.xml` | staining procedures, compounds, targets |

It builds ID-chain mappings (image→slide→block→specimen→case→biological being) and joins attributes into `BigpictureBlockFields`. `age_at_extraction` is stored as an ISO-8601 duration tuple `(start, end)` — e.g. `("P40Y", "P41Y")` — computed by `_add_iso8601_durations` (uses `isodate`, normalises month overflow). Invalid durations are logged and return `None`.

### Domain models (`search_api/bigpicture/models.py`)

```
BigpictureCodeAttributeValue          frozen — code, scheme, meaning, scheme_version
BigpictureSampleBiologicalBeingFields — species, sex
BigpictureSampleSpecimenFields        — anatomical_site: frozenset[…], fixation_type,
                                        fixation_type_text, specimen_type,
                                        age_at_extraction: tuple[str, str] | None
BigpictureSampleBlockFields           — block_preparation
BigpictureBlockFields                 frozen, inherits all three above
BigpictureStainingFields              frozen — staining_procedure, _text, _compound, _compound_text, _target
BigpictureFields                      — image_id, dataset_id, dataset_image_cnt,
                                        dataset_short_name/title/description,
                                        blocks: set[BigpictureBlockFields],
                                        stains: set[BigpictureStainingFields]
```

`BigpictureBlockFields` is stored in a `set`, so it must be hashable — hence `frozen=True` on the model and `frozenset` for `anatomical_site`. The `@field_serializer` on `anatomical_site` serialises it as `list[dict]` (Pydantic's default `set[dict]` is unhashable and not JSON-serialisable).

### API layer (`search_api/api/`)

- `api/beacon/models.py` — Beacon V2 request/response Pydantic models shared across beacons
- `api/bigpicture/models.py` — Bigpicture-specific static data: `BP_FILTERING_TERMS`, `BP_OPENSEARCH_FIELD` mapping, `FieldValueCount`
- `api/bigpicture/routes.py` — FastAPI router:

| Endpoint | Description |
|---|---|
| `GET /info` | Beacon metadata |
| `GET /filtering_terms` | Available filter definitions |
| `POST /query` | Beacon V2 search |
| `POST /ai/query` | Natural-language search (pydantic-ai + Ollama) |
| `GET /fields/{field_id}/values` | Top indexed values with counts; resolves concept IDs to preferred SNOMED terms for ontology fields |
| `GET /fields/{field_id}/suggestions` | SNOMED autocomplete restricted to indexed values |
| `GET /health` | Health check |

- `api/bigpicture/services/beacon.py` — `BigpictureBeaconService` ABC with `OpenSearchBigpictureBeaconService` (real) and `MockBigpictureBeaconService` (hardcoded test data)
- `api/bigpicture/services/ai.py` — pydantic-ai agent that maps natural-language queries to Beacon filters

### Query path

`POST /query` receives a `BeaconQueryRequest`. Each filter's `id` is looked up in `BP_OPENSEARCH_FIELD` to resolve one or more OpenSearch field paths. The filter's `type` (`BeaconFilteringTermType`) determines the query builder:

| type | builder | notes |
|---|---|---|
| `text` | `build_match_query` | full-text match |
| `controlledValue` | `build_term_query` | exact keyword match |
| `ontology` | `build_term_query` | exact concept ID match |
| `ontologyOrValue` | `build_term_query` | exact match, ontology or free value |
| `iso8601Range` | `build_iso8601_range_query` | ISO-8601 duration range (e.g. `P40Y-P50Y`), converted to days |

When a filter maps to multiple OpenSearch fields they are combined with `or_queries`. Filters on `blocks` or `stains` are wrapped in nested queries automatically. The response granularity (`boolean` / `count` / `resultSets`) is determined by `request.query.requestedGranularity`.

### SNOMED service (`search_api/services/snomed.py`)

`SnomedService` wraps the Snowstorm API:

- `find_concept(term, ecl, branch)` → concept ID or `None`
- `search_concepts(term, ecl, branch, limit)` → `list[SnomedConcept]`
- `get_descendants(concept_id, branch)` → `list[SnomedConcept]` (static)
- `get_preferred_terms(concept_ids, ecl, branch)` → `dict[str, str]` (static, used by `/values`)
- `suggest_concepts(term, field_id, ecl, branch, limit, prefix_match)` → `list[SnomedConcept]` (used by `/suggestions`, filters by indexed values via `IndexedConceptIdProvider` protocol)

`_fetch_all_concepts` is cached for 30 days; `fetch_indexed_keywords` for 4 hours.

### OpenSearch index mapping highlights

- `blocks` and `stains` are **nested** types
- All code/keyword fields are `keyword` (support array values natively)
- `age_at_extraction` is `integer_range` — stored as `{gte: <days>, lte: <days>}` converted from ISO-8601 strings by `_convert_iso8601_range_for_opensearch` (uses `iso8601_duration_to_days`: 1 year = 365 days, 1 month = 30 days; logs error and drops field on invalid input)
- `dataset_title/description/short_name` use `english_text` analyzer

### Connection details (hardcoded defaults — see `conf.py`)

- Postgres: `localhost:5434`, db `sd_search`, user `postgres`, password `test`
- OpenSearch: `host.docker.internal:9200`, user `admin`, password `Sd@Search9x!`, SSL on, certs not verified
- Snowstorm: empty by default; set `SNOWSTORM_URL` env var
- LLM (Ollama): `http://localhost:11434/v1`, model `qwen2.5:14b`

## Tests

```
tests/
├── unit/          # run by tox; no external services needed
├── integration/   # require OpenSearch; conftest.py creates/tears down a UUID-named test index
│   └── services/  # SNOMED tests marked @pytest.mark.external (require SNOWSTORM_URL)
├── performance/   # locust load tests
└── files/bigpicture/
    └── xml/dataset_1/METADATA/          # XML fixtures for process.py tests
```

The integration `conftest.py` provides three module-scoped fixtures: `opensearch_docs` (override to supply inline documents), `opensearch_index_name` (returns `bp-image-index-test-<uuid>` so each run gets an isolated index), and `opensearch_index` (creates, loads, and tears down the index). Test modules override `opensearch_docs` with their own inline data — no external JSON file is needed.
