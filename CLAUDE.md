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

# Run the API server (dev) — DEPLOYMENT_TYPE selects the deployment
DEPLOYMENT_TYPE=Bigpicture uvicorn search_api.main:app --reload
# or via the installed script
DEPLOYMENT_TYPE=Bigpicture sd_search_api

# Start Postgres + OpenSearch (required for data loading/sync)
docker compose --env-file tests/integration/.env --profile dev up --build

# Admin CLI (load data, generate index, refresh SNOMED cache)
uv run python scripts/admin.py Bigpicture load <dir> --load --sync
uv run python scripts/admin.py Bigpicture generate-index
uv run python scripts/admin.py snomed refresh
```

Dependencies are managed with `uv`. The virtualenv is at `.venv/`. Most config is supplied
via environment variables (see `tests/integration/.env` for a working set).

## Architecture

A **FastAPI** app implementing the **Beacon V2** protocol. The codebase is **deployment-agnostic**:
all deployment-specific behaviour is captured in a `Domain`, and the generic machinery (router,
lifespan, load/sync, ontology resolution) is built from it. Bigpicture (digital pathology image
search) is currently the only deployment.

### Deployment seam: `Domain` (`search_api/api/domain.py`)

`main.py` selects one deployment by the `DEPLOYMENT_TYPE` env var, looks it up in the registry
(`api/deployments.py` `DOMAINS`), and builds the app from it: `make_beacon_router(domain)` +
`make_lifespan(domain)`. The admin router mounts only when `ADMIN_KEY` is set.

`Domain` (frozen dataclass) carries everything a deployment varies:

```
name, opensearch_index, beacon_id, beacon_name, schemas
filtering_terms, non_filtering_fields          # OpenSearch field config
loader: Loader[…]                              # how source data is ingested
beacon_service_factory                         # builds the BeaconService for a search client
result_sets_response_model                     # deployment's Beacon resultSets shape
ai_assistant_description, ai_result_model, ai_result_instructions   # AI search persona + output
```

- `Domain.ontology_ids` → distinct `ontology.id`s referenced by the filtering terms.
- `make_lifespan` builds **one term cache per ontology** via `create_term_caches(domain.ontology_ids)`
  and stores them as `app.state.ontology_term_services: dict[ontology_id, OntologyTermCacheService]`.
- `Loader[LoadOptionsT]` (generic) bundles a deployment's `add_load_options` / `parse_load_options`
  / `extract` callables for the admin CLI.

A new deployment = a new `Domain` registered in `DOMAINS` (see `api/bigpicture/domain.py` for the
pattern: `BP_DOMAIN`, `BP_LOADER`).

### Dual-backend data flow

1. **PostgreSQL** (`database/repository.py`, `database/document.py`) — primary store. The generic
   `document` table holds `id`, `payload` (JSONB, already in OpenSearch shape), `modified_at`,
   `synced_at`. `LoadService` (`services/load.py`) stores extracted documents (skipping any not
   newer than what's stored), and caches ontology preferred terms as it goes. Rows with
   `synced_at IS NULL` are pending sync.

2. **OpenSearch** (`api/opensearch/services.py`) — the search index (`bp-image-index` for Bigpicture).
   `SyncService` (`services/sync.py`, constructed with the index name) reads unsynced rows, bulk-indexes
   via `index_documents`, then stamps `synced_at`.

The OpenSearch-shaped payload is produced at **load** time by `build_document`
(`api/opensearch/document.py`), which converts each `OpenSearchFieldValue`; `age_at_extraction`
ISO-8601 duration tuples become `{gte, lte}` day ranges via `iso8601_duration_to_days`.

### XML ingestion (`search_api/bigpicture/services/extract.py`)

`extract_documents(root, single_dir, c4gh_private_key_file, c4gh_passphrase) → Iterator[ExtractedDocument]`
walks a directory tree and reads four XML files per dataset:

| File | Extracts |
|---|---|
| `METADATA/dataset.xml` | dataset ID, title, description |
| `METADATA/image.xml` | image IDs, slide mappings |
| `METADATA/sample.xml` | biological beings, cases, specimens, blocks |
| `METADATA/staining.xml` | staining procedures, compounds, targets |

It builds ID-chain mappings (image→slide→block→specimen→case→biological being), parses into the
Bigpicture models below, then `to_opensearch_field_values(fields)` flattens them to
`OpenSearchFieldValue`s keyed by the fields declared in `BP_DOCUMENT_FIELDS`. `age_at_extraction`
is an ISO-8601 duration tuple `(start, end)` — e.g. `("P40Y", "P41Y")` — computed by
`_add_iso8601_durations` (uses `isodate`, normalises month overflow); invalid durations are logged
and dropped. `.c4gh`-encrypted XML is decrypted on the fly (`services/crypt.py`).

The Bigpicture parsing models (also in `extract.py`):

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

`BigpictureBlockFields` is stored in a `set`, so it must be hashable — hence `frozen=True` and
`frozenset` for `anatomical_site`. The `@field_serializer` on `anatomical_site` serialises it as
`list[dict]` (Pydantic's default `set[dict]` is unhashable and not JSON-serialisable).

### Configurable fields (YAML)

Indexed fields and filtering terms are declared in `api/bigpicture/fields.yaml` and loaded by
`api/fields.py` (`load_fields_config`) into `BP_DOCUMENT_FIELDS` / `BP_FILTERING_TERMS`
(`api/bigpicture/models.py`). The OpenSearch index mapping JSON is generated from these fields by
`OpenSearchIndexGeneratorService` (`api/opensearch/index_generator.py`) via the
`generate-index` admin command.

### API layer & routes

The Beacon router is generic, built per domain by `make_beacon_router(domain)`
(`api/beacon/routes.py`). Dependency providers (`get_beacon_service`, `get_ai_service`,
`get_ontology_term_services`) resolve services from `app.state` and are module-level so tests can
override them via `app.dependency_overrides`.

| Endpoint | Description |
|---|---|
| `GET /info` | Beacon metadata |
| `GET /filtering_terms` | Available filter definitions |
| `POST /query` | Beacon V2 search |
| `POST /ai/query` | Natural-language search (gated by `FEATURE_AI`) |
| `GET /filtering_terms/{field_id}/values` | Indexed values with counts; ontology fields resolve concept IDs to preferred terms |
| `GET /filtering_terms/{field_id}/suggestions` | Autocomplete restricted to indexed values |
| `GET /health` | Health check |

Admin routes (`api/admin/routes.py`, `/admin` prefix, mounted only when `ADMIN_KEY` set, SNOMED-specific):
`/admin/snomed/reload`, `/admin/snomed/refresh`, `/admin/snomed/fields/{field_id}/invalid_concepts`,
`/admin/snomed/fields/{field_id}/unexpected_concepts`.

The BeaconService abstraction lives in `api/beacon/services.py` (`BeaconService` ABC,
`OpenSearchBeaconService` generic base); the Bigpicture implementation is
`BigpictureOpenSearchBeaconService` (`api/bigpicture/opensearch.py`).

### Query path

`POST /query` receives a `BeaconQueryRequest`. Ontology filters (`ontology` / `ontologyOrValue`)
are resolved first: per filter, `get_ontology_service(term.ontology.id).prepare_ontology_filter(...)`
turns free text / concept IDs into concept IDs (optionally expanding to descendants when
`includeDescendantTerms`). The filter `type` (`BeaconFilteringTermType`) then selects the query
builder (`api/opensearch/services.py`):

| type | builder | notes |
|---|---|---|
| `text` | `build_match_query` | full-text match |
| `controlledValue` / `keyword` | `build_term_query` / `build_terms_query` | exact keyword match |
| `ontology` / `ontologyOrValue` | `build_term_query` | exact concept-ID match |
| `iso8601Range` | `build_iso8601_range_query` | ISO-8601 duration range, converted to days |

Filters mapping to multiple OpenSearch fields are combined with `or_queries`; filters on `blocks`
or `stains` are wrapped in nested queries. Response granularity (`boolean` / `count` / `record`)
comes from `request.query.requestedGranularity`.

### Ontology providers (`search_api/services/ontology.py`)

`OntologyService` (ABC) abstracts term resolution for one ontology: `is_concept_id`,
`get_preferred_terms`, `prepare_ontology_filter`. A registry maps an **ontology id** (e.g. `SCTID`)
to its provider via `register_ontology_service` / `get_ontology_service`. **Providers self-register
at import time** — `ontology.py` stays unaware of concrete providers; a deployment pulls in the
provider modules it uses (Bigpicture reaches `services/snomed.py` transitively). A filtering term
selects its provider by `ontology.id`.

`services/snomed.py` — `SnomedService(OntologyService)` wraps Snowstorm and self-registers
`SCTID` (both the service and its term-cache factory):

- `find_concept(term, ecl, branch)` → `SnomedConcept | None`
- `search_concepts(term, ecl, branch, limit)` → `list[SnomedConcept]`
- `find_descendants(concept_id, branch)` → `list[SnomedConcept]` (static)
- `get_preferred_terms(concept_ids, branch)` → `dict[str, str]`
- `get_concepts(concept_ids, ecl, branch)` → `dict[str, SnomedConcept]` (static)
- `suggest_concepts(term, ecl, branch, limit, indexed_concept_ids)` → `list[SnomedConcept]`
- `prepare_ontology_filter(query_filter, filtering_terms, branch)` → resolved `BeaconQueryFilter`

`_fetch_all_concepts` is cached for 30 days. Set `SNOWSTORM_URL` to enable.

### Ontology term cache (`search_api/services/ontology_term.py`)

A persistent cache mapping `(field_id, concept_id)` → preferred term, served from an in-memory dict
reloaded from Postgres on a background interval.

- `OntologyTermCacheService` (ABC) — `load`, `get_preferred_terms`, `cache_preferred_terms`,
  `refresh`, plus no-op `start`/`stop` lifecycle hooks.
- `PostgresOntologyTermCacheService(table)` — concrete, parameterised by table name; overrides
  `start`/`stop` to run the refresh loop.
- `SnomedPostgresOntologyTermCacheService` — preconfigured for the `snomed` table.
- A factory registry (`register_term_cache` / `create_term_caches`) mirrors the ontology-service
  registry; `make_lifespan` builds one cache per ontology in play.

### AI search (`search_api/ai/`)

`AIService` (`ai/services.py`) is a generic pydantic-ai agent (Ollama by default). It's
parameterised by `result_model` (the agent's structured `output_type`) and `result_instructions`
(step 3 of the system prompt). The generic prompt covers the tool flow (`get_filtering_terms`, then
the `query` tool) and a scope-constraint block; deployments supply the persona and result shape via
`Domain`. `ai/models.py` holds the generic base `AISearchResult` (`interpretation`, `filters`) and
`AIQueryFilter`. Bigpicture's result shape (`BigpictureAISearchResult` with `dataset_count` +
nested `Dataset`) and prompt fragments live in `api/bigpicture/ai.py`. The agent's `output_type`
constrains the LLM to emit JSON matching the model; `result.output` is the concrete subclass.

### OpenSearch index mapping highlights

- `blocks` and `stains` are **nested** types
- All code/keyword fields are `keyword` (support array values natively)
- `age_at_extraction` is `integer_range` — stored as `{gte: <days>, lte: <days>}` (1 year = 365
  days, 1 month = 30 days; invalid input logged and dropped)
- `dataset_title/description/short_name` use the `english_text` analyzer

### Configuration (`conf.py`)

Settings come from the environment (pydantic-settings); most fields are **required** (no hardcoded
host/db/password defaults). Defaults that exist: `POSTGRES_PORT=5432`, `OPENSEARCH_PORT=9200`,
`DEPLOYMENT_ENV=dev`, `SNOMED_CACHE_REFRESH=300`, `FEATURE_AI=false`, `ADMIN_KEY=None` (admin
endpoints unmounted when unset). `DEPLOYMENT_TYPE`, `SNOWSTORM_URL`, and `LLM_BASE_URL`/`LLM_API_KEY`
have no defaults. A working set is in `tests/integration/.env`.

## Tests

```
tests/
├── unit/          # run by tox; no external services needed
│   ├── api/{admin,beacon,bigpicture,opensearch}/
│   ├── bigpicture/services/
│   └── services/
├── integration/   # require Postgres/OpenSearch (route tests hit a running server)
│   ├── api/bigpicture/        # endpoint tests, incl. AI (test_routes_ai.py, @skip — needs Ollama)
│   ├── bigpicture/services/   # extract + load against Postgres
│   ├── database/, scripts/bigpicture/
│   └── services/              # SNOMED term cache; SNOMED tests marked @pytest.mark.external
├── performance/   # locust load tests
└── files/bigpicture/xml/dataset_1/METADATA/   # XML fixtures
```

Integration `conftest.py` loads `tests/integration/.env` and provides module-scoped fixtures:
`bp_opensearch_docs` (override to supply inline documents), `bp_opensearch_index_name` (returns a
`bp-image-index-test-<uuid>` so each run is isolated), and `bp_opensearch_index` (creates, loads,
tears down). Test modules override `bp_opensearch_docs` with inline data — no external JSON file needed.
