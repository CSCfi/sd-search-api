# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run all checks (ruff format+lint, mypy, pytest)
tox

# Individual checks
tox -e ruff     # format and lint
tox -e mypy     # type check
tox -e pytest   # unit tests with coverage

# Run a single test
.venv/bin/pytest tests/unit/path/to/test_file.py::test_name -x

# Run the API server (dev)
uvicorn search_api.main:app --reload
# or via the installed script
sd_search_api

# Start Postgres (required for data loading/sync)
docker compose --profile dev up --build
```

Dependencies are managed with `uv`. The virtualenv is at `.venv/`.

## Architecture

The API is a **FastAPI** app (`search_api/main.py`) that implements the **Beacon V2** protocol for Bigpicture image search. It has two separate backends:

### Dual-backend data flow

1. **PostgreSQL** (`search_api/database/repository.py`, `search_api/bigpicture/service.py`) — primary store. Images are loaded via `load_fields()` into the `bp_image` table (JSONB columns for `blocks`/`stains`). A `search_sync` flag tracks which rows need syncing.

2. **OpenSearch** (`search_api/services/search.py`) — search index (`bp-image-index`). `sync_fields()` reads unsynced rows from Postgres, bulk-indexes them to OpenSearch in batches of 1000, then marks them synced.

### API layer (`search_api/api/`)

- `api/beacon/models.py` — Beacon V2 request/response Pydantic models (shared across beacons)
- `api/bigpicture/models.py` — Bigpicture-specific static data: beacon ID, filtering terms, info/response objects
- `api/bigpicture/routes.py` — FastAPI router with endpoints: `GET /info`, `GET /filtering_terms`, `POST /query`, `GET /health`
- `api/bigpicture/services.py` — `BigpictureBeaconService` ABC with two implementations:
  - `OpenSearchBigpictureBeaconService` — real OpenSearch queries
  - `MockBigpictureBeaconService` — returns hardcoded test data

### Query path

`POST /query` receives a `BeaconQueryRequest`. Filters are mapped via `BP_OPENSEARCH_FIELD` to OpenSearch fields, then dispatched to `build_match_query` (text), `build_term_query` (ontology/vocabulary), or `build_range_query` (numberRange). Filters on blocks/stains use OpenSearch nested queries. The response granularity (`boolean` / `count` / `resultSets`) is determined by `request.query.requestedGranularity`.

### Connection details (hardcoded — see TODOs)

- Postgres: `localhost:5432`, db `sd_search`, user `postgres`, password `test`
- OpenSearch (services): `localhost:9200`, auth `admin/admin`
- OpenSearch (routes/Docker): `host.docker.internal:9200`
