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

# Admin CLI (load data, generate index, refresh an ontology)
# `<ontology> refresh` has two parts: update the ontology from its source, then
# refresh the preferred terms cached for it in terms_cache.
uv run python scripts/admin.py Bigpicture load <dir> --load --sync
uv run python scripts/admin.py Bigpicture generate-index
uv run python scripts/admin.py Bigpicture create-index   # once per environment
uv run python scripts/admin.py snomed refresh --release-file <path/to/SnomedCT_*.zip>
uv run python scripts/admin.py send refresh
```

Dependencies are managed with `uv`. The virtualenv is at `.venv/`. Most config is supplied
via environment variables (see `tests/integration/.env` for a working set).

## Architecture

A **FastAPI** app implementing the **Beacon V2** protocol. The codebase is **deployment-agnostic**:
all deployment-specific behaviour is captured in a `Domain`, and the generic machinery (router,
lifespan, load/sync, ontology resolution) is built from it. Bigpicture (digital pathology image
search) is currently the only deployment.

### Package layout

```
search_api/
├── api/                # HTTP layer + per-deployment packages
│   ├── {admin,auth,beacon,opensearch}/      # generic routers and services
│   └── bigpicture/     # everything Bigpicture-specific lives here, nowhere else:
│       ├── domain.py models.py ai.py opensearch.py extract.py
│       ├── config/     # hand-edited fields/groups/scopes YAML
│       ├── index/      # GENERATED OpenSearch mapping (generate-index writes it)
│       └── schemas/    # XSDs used to validate the ingested XML
├── services/           # generic services
│   ├── ontology/       # service.py registrations.py term_cache.py cached.py snomed.py send.py
│   ├── auth.py session.py
│   └── load.py sync.py
├── utils/              # stateless helpers: crypt.py dir.py xml.py
├── ai/  database/  conf.py  exceptions.py  main.py
```

All deployment files sit under one directory, so every data path resolves with a plain
`Path(__file__).parent / …` — no traversal back out of the package. `config/` vs `index/` marks
which files are hand-edited and which are generated.

### Deployment seam: `Domain` (`search_api/api/domain.py`)

`main.py` selects one deployment by the `DEPLOYMENT_TYPE` env var, looks it up in the registry
(`api/deployments.py` `DOMAINS`), and builds the app from it: `make_beacon_router(domain)` +
`make_lifespan(domain)`. The admin router mounts only when `ADMIN_KEY` is set.

`Domain` (frozen dataclass) carries everything a deployment varies:

```
name, opensearch_index, beacon_id, beacon_name, schemas
filtering_terms, filtering_groups, filtering_scopes, non_filtering_fields   # field config
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

The OpenSearch-shaped payload is produced at **load** time by `build_document(document)`
(`api/opensearch/document.py`), which converts each `OpenSearchFieldValue`; `age_at_extraction`
ISO-8601 duration tuples become `{gte, lte}` day ranges via `iso8601_duration_to_days`.

`ExtractedDocument` carries three things beyond its values: `id`, `modified_at` and **`scope`**.
Scope is not a filtering term — it partitions documents rather than being searched — so it lives on
the document, is indexed at the root under `SCOPE_FIELD` (`api/scopes.py`), and `Domain`
contributes that field to `opensearch_fields` whenever the deployment declares any scope.
An `OpenSearchFieldValue` additionally carries **`qualifiers`** (`{qualifier id: [values]}`); the
qualifiers of every value sharing a nested `(group, index)` are merged into that one nested item, so
stating them on a single value of the item is enough.

`LoadService.validate_document` is the boundary where a deployment's extraction meets the generic
store: it checks `scope` against `filtering_scopes` and every qualifier id/value against
`filtering_qualifiers`, so each extractor does not restate the declared configuration.

### XML ingestion (`search_api/api/bigpicture/extract.py`)

`extract_documents(root, fs, single_dir, c4gh_private_key_file, c4gh_passphrase) → Iterator[ExtractedDocument]`
walks a directory tree and reads six XML files per dataset:

| File | Extracts |
|---|---|
| `METADATA/dataset.xml` | dataset ID, title, description |
| `METADATA/image.xml` | image IDs, slide mappings |
| `METADATA/policy.xml` | `scope` (see below) |
| `METADATA/sample.xml` | biological beings, cases, specimens, blocks |
| `METADATA/staining.xml` | staining procedures, substances, targets |
| `METADATA/observation.xml` | `diagnosis` and `finding` groups, each with the `observation` qualifier (optional file) |

`scope` comes from the policy's `type_of_dataset` attribute, whose value is
`"<scope>/<de-identification>"` (`Clinical/Anonymized`, `Clinical/Pseudonymized`,
`Non-Clinical/Obscured`, `Non-Clinical/Cryptonymized`). Only the part before the `/` is read, so a
new de-identification method needs no code change; that part is matched case-insensitively, while
the tag itself is not (it is the spec's machine name, read via `_extract_string_attribute_value`).
`_extract_scope` raises `UserException` if the attribute is missing or its scope is not
`Clinical`/`Non-Clinical`.

It builds ID-chain mappings (image→slide→block→specimen→case→biological being), parses into the
Bigpicture models below, then `to_opensearch_field_values(fields)` flattens them to
`OpenSearchFieldValue`s keyed by the fields declared in `BP_DOCUMENT_FIELDS`. `age_at_extraction`
is an ISO-8601 duration tuple `(start, end)` — e.g. `("P40Y", "P41Y")` — computed by
`_add_iso8601_durations` (uses `isodate`, normalises month overflow); invalid durations are logged
and dropped. `.c4gh`-encrypted XML is decrypted on the fly (`utils/crypt.py`).

The parsing models are also in `extract.py`; `BigpictureFields` is the per-image root, holding the
ids, `scope`, the dataset fields, and the `specimen`, `staining`, `diagnosis` and `finding` sets. `BigpictureSpecimenFields` flattens the biological being, specimen and block
fields into one model (see the grouping rationale in `fields.yaml`).

`scope` is not in `fields.yaml` — it is `ExtractedDocument.scope`, produced by `_extract_scope`.

An observation statement contributes to the `diagnosis` or `finding` set depending on its
`STATEMENT_TYPE`; the parsing is otherwise identical, including how statements reach images. Both
carry the `observation` qualifier (`confirmed` / `candidate`, constants in `extract.py`): a statement
linked by `IMAGE_REF`, or whose `STATEMENT_STATUS` is `Distinct`, is `confirmed` for the image, and
anything reaching several images through another ref is a `candidate` for each. Each statement yields
**one nested item**, carrying the single qualifier value that statement was made under; items are not
merged across statements. The group is a `set`, so a statement repeated verbatim for an image
collapses. The same codes stated `confirmed` by one statement and `candidate` by another therefore
appear as two items — which no consumer can see, since facet counts are document counts.

Because `specimen` and `staining` are held in `set`s their models must be hashable — hence
`frozen=True`, and `frozenset` for `anatomical_site`. The `@field_serializer` on `anatomical_site`
serialises it as `list[dict]` (Pydantic's default `set[dict]` is unhashable and not
JSON-serialisable).

### Configurable fields (YAML)

Indexed fields and filtering terms are declared in `api/bigpicture/config/fields.yaml` and loaded by
`api/fields.py` (`load_fields_config`) into `BP_DOCUMENT_FIELDS` / `BP_FILTERING_TERMS`
(`api/bigpicture/models.py`). The OpenSearch index mapping JSON is generated from these fields by
`OpenSearchIndexGeneratorService` (`api/opensearch/index_generator.py`) via the
`generate-index` admin command.

An `ontology` / `ontologyOrValue` field may declare an `ontologyRestriction` — the part of the
ontology its values may resolve to. It is deployment configuration, excluded from both API
responses and the OpenAPI schema. It is optional: a field without one resolves against the whole
ontology (`snomed_ecl` is then `None`), which is why the provider hooks verify their match.

```yaml
ontologyRestriction:
  concept_ids: [ "410607006" ]   # Organism
  include_descendants: true      # the subtree; false means exactly these concepts
```

### Filtering qualifiers (`api/qualifiers.py`, `config/qualifiers.yaml`)

A **qualifier** is a second axis on the values of a nested group. Where a scope partitions
documents, a qualifier labels the values *within* a group. The indexed field is multivalued, so a
group item can carry several qualifier values, though Bigpicture sets exactly one per item. This
replaces duplicating a field into known/candidate copies:
Bigpicture declares one qualifier, `observation` (`confirmed` / `candidate`), over the `diagnosis`
and `finding` groups.

```yaml
filtering_qualifiers:
  - id: observation
    values: [ confirmed, candidate ]
    groups: [ diagnosis, finding ]   # nested groups whose values it qualifies
```

A qualifier is **not** a filtering term, so it is absent from `BP_DOCUMENT_FIELDS`. Every nested
group holds all of its qualifier values in **one** multivalued `keyword` field,
`<group>.qualifiers` (`QUALIFIERS_FIELD`), with each value encoded as `<qualifier id>:<value>` by
`encode_qualifier_value` — e.g. `observation:confirmed`. `Domain.opensearch_fields` emits that field
for **every** nested group, not only the qualified ones, so declaring a qualifier — or applying an
existing one to another group — never requires an index recreate.

`groups` therefore no longer drives the mapping; it declares where a qualifier is *meaningful*, which
the query side still needs: applying a qualifier clause to a group that carries no qualifier values
would match nothing and silently zero the results.

`validate_filtering_qualifiers` rejects a qualifier naming an unknown group, a duplicate id, a
filtering term that would collide with the reserved `qualifiers` field, and an id or value containing
the reserved `:` separator.

A query restricts a qualifier via `BeaconQuery.requestedQualifiers` (`{qualifier id: [values]}`) or,
on the values/suggestions endpoints, a repeatable `qualifier=<id>:<value>` param. **A qualifier that
is absent is not filtered on**, so all of its values match — there is no default.
`validate_requested_qualifiers` checks ids and values, and is shared by the router and the loader.

### API layer & routes

The Beacon router is generic, built per domain by `make_beacon_router(domain)`
(`api/beacon/routes.py`). Dependency providers (`get_beacon_service`, `get_ai_service`,
`get_ontology_term_services`) resolve services from `app.state` and are module-level so tests can
override them via `app.dependency_overrides`.

| Endpoint | Description |
|---|---|
| `GET /info` | Beacon metadata |
| `GET /filtering_terms` | Available filter definitions |
| `GET /filtering_groups` | UI groupings of filtering terms |
| `GET /filtering_scopes` | Available scopes (e.g. `clinical` / `non_clinical`) |
| `GET /filtering_qualifiers` | Available qualifiers of the values in a nested group |
| `POST /query` | Beacon V2 search |
| `POST /ai/query` | Natural-language search (gated by `FEATURE_AI`) |
| `GET /filtering_terms/{field_id}/values` | Indexed values with counts; ontology fields resolve concept IDs to preferred terms |
| `GET /filtering_terms/{field_id}/suggestions` | Autocomplete restricted to indexed values |

`values` and `suggestions` both accept `scope=<id>` and repeatable `qualifier=<id>:<value>`, and
restrict their counts by both. Neither has a default: omitting one does not filter on it. A scope the
field does not declare is a `400` rather than an empty list, since the field is never indexed for
those documents (`validate_field_scope`).
| `GET /health` | Health check |

Admin routes (`api/admin/routes.py`, `/admin` prefix, mounted only when `ADMIN_KEY` set, SNOMED-specific):
`/admin/snomed/reload`, `/admin/snomed/refresh`, `/admin/snomed/fields/{field_id}/invalid_concepts`,
`/admin/snomed/fields/{field_id}/unexpected_concepts`.

Auth routes (`api/auth/routes.py`, always mounted): `GET /login`, `GET /callback`, `GET /logout` —
an OIDC relying party (`services/auth.py`) that issues a session JWT cookie. Configured by
`OIDCConfiguration` / `JWTConfiguration` in `conf.py`.

The BeaconService abstraction lives in `api/beacon/services.py` (`BeaconService` ABC,
`OpenSearchBeaconService` generic base); the Bigpicture implementation is
`BigpictureOpenSearchBeaconService` (`api/bigpicture/opensearch.py`).

### Query path

`POST /query` receives a `BeaconQueryRequest`. Ontology filters (`ontology` / `ontologyOrValue`)
are resolved first: per filter, `get_ontology_service(term.ontology.id).prepare_ontology_filter(...)`
turns free text / concept IDs into concept IDs (optionally expanding to descendants when
`includeDescendantTerms`), scoped by the field's `ontologyRestriction` — see the resolution cascade
under *Ontology providers*. The filter `type` (`BeaconFilteringTermType`) then selects the query
builder (`api/opensearch/services.py`):

| type | builder | notes |
|---|---|---|
| `text` | `build_match_query` | full-text match |
| `controlledValue` / `keyword` | `build_term_query` / `build_terms_query` | exact keyword match |
| `ontology` / `ontologyOrValue` | `build_term_query` | exact concept-ID match |
| `iso8601Range` | `build_iso8601_range_query` | ISO-8601 duration range, converted to days |

Filters mapping to multiple OpenSearch fields are combined with `or_queries`; filters on a nested
group (`specimen`, `staining`, `diagnosis`, `finding`) are wrapped in nested queries. A requested
qualifier adds a `terms` clause **inside** the nested query of each group it qualifies, so it must
hold for the very nested item that matched rather than for any item in the group. A group that no
filter targets gets no nested query, so a qualifier alone never constrains it. `get_indexed_field_value_counts(field_id, scope, qualifiers)` applies both restrictions to the facet
aggregation so counts match what the equivalent query returns: `scope` becomes the aggregation's
document `query`, and the qualifier clause becomes `fetch_indexed_keywords`'s `group_item_filter`. Counts
therefore never overlap — with neither given everything is counted, and a qualifier is only applied
to the groups that declare it, so it cannot zero an unqualified group's counts.

**Counts are document counts.** A bucket inside a `nested` aggregation counts group items, so a
`reverse_nested` sub-aggregation (`documents`) climbs back to the documents holding them and its
count is the one returned. Without it an image with two `Female` specimens would count twice for
`Female`. Top-level fields need no reverse_nested — their buckets already count documents. Response granularity (`boolean` / `count` / `record`)
comes from `request.query.requestedGranularity`.

### Ontology providers (`search_api/services/ontology/service.py`)

`OntologyService` (ABC) abstracts term resolution for one ontology: `is_concept_id`,
`get_preferred_terms`, and `prepare_ontology_filter`. `prepare_ontology_filter` is a template
method implemented once on the ABC — filtering-term lookup, value normalisation, the
resolved/unresolved split, and the final filter rebuild are identical across providers. Each
provider only implements two hooks: `_find_concept_ids(value, filtering_term)` (one value ->
its concept ID(s), possibly more than one if a term isn't unique) and
`_find_descendant_ids(concept_ids)` (a set of concept IDs -> all of their descendants).

Each value is resolved by `_resolve_concept_ids`, cheapest source first:

1. **A concept id is taken as given** — an id absent from the ontology is absent from the index
   too, so looking it up would cost a round trip without changing the result.
2. **A preferred term cached for the field** (`terms_cache`, matched via `normalise_term`, so case-
   and space-insensitively) resolves in memory. Unlike the field's `ontologyRestriction` this covers
   every concept actually indexed for the field, including ones outside the restriction.
3. Otherwise the provider's `_find_concept_ids` hook consults the ontology itself.

Unresolved values are only kept in the prepared filter for `ontologyOrValue` fields (which have a
free-text fallback field downstream); for strict `ontology` fields they're dropped. A registry maps
an **ontology id** (e.g. `SCTID`) to its provider via `register_ontology_service` /
`get_ontology_service`, keeping `ontology.py` unaware of concrete providers. A filtering term
selects its provider by `ontology.id`.

**Every provider is registered in `services/ontology/registrations.py`** — both the ontology service and its
preferred term cache factory, for SNOMED and SEND alike. `api/domain.py` imports that module for
its side effects, so the registries are populated before a domain resolves or initialises an
ontology. Registering a new provider means adding it there, not adding an import somewhere.

`services/ontology/snomed.py` — `SnomedService(OntologyService)` wraps Snowstorm; registered as `SCTID`:

- `find_concept(term, ecl, branch)` → `SnomedConcept | None`
- `find_descendants(concept_id, branch)` → `list[SnomedConcept]` (static)
- `get_preferred_terms(concept_ids, branch)` → `dict[str, str]`
- `_find_concept_ids`/`_find_descendant_ids` — the two `OntologyService` hooks, built on
  `find_concept`/`find_descendants` above (always on the `"MAIN"` branch — no caller varies it).
  Snowstorm always returns its best match, so `_find_concept_ids` accepts one only if the value
  **is** one of the concept's descriptions, compared by `normalise_term` (`_describes`); otherwise
  an unrecognised value would resolve to a loosely related concept and silently match no documents.
  The comparison is deliberately exact rather than partial — a partial term like "Formalin" is
  rejected even though it appears in "Neutral buffered formalin 10% solution", because the concept
  a value resolved to is not reported back to the caller.

`_fetch_all_concepts` and `_fetch_descriptions` are cached for 30 days. Set `SNOWSTORM_URL` to
enable.

`import_snomed_release(release_file, branch)` automates the README's "Import SNOMED release"
procedure: creates a Snowstorm import job, uploads the release archive, then polls
`/imports/{id}` until it reports `COMPLETED` (raising on `FAILED`; a `404` also means done, per
Snowstorm's own behaviour of dropping completed jobs). Invoked by `scripts/admin.py`'s
`snomed refresh --release-file <path>`, where `--release-file` is required.

### Ontology term cache (`search_api/services/ontology/term_cache.py`)

A persistent cache mapping `(ontology_id, field_id, concept_id)` → preferred term, served from an
in-memory dict reloaded from Postgres on a background interval. Appropriate for large ontologies
(e.g. SNOMED CT) where storing every concept is infeasible — only concepts actually observed while
indexing a field are cached. All ontologies share the one `terms_cache` table
(`ontology_id, concept_id, field_id, preferred_term, updated_at`).

- `OntologyTermCacheService` (ABC) — `load`, `get_preferred_terms`, `cache_preferred_terms`,
  `refresh`, plus no-op `start`/`stop` lifecycle hooks.
- `PostgresOntologyTermCacheService(ontology_id)` — concrete, parameterised by ontology id; queries
  the shared `terms_cache` table filtered by that id; overrides `start`/`stop` to run the refresh loop.
- A factory registry (`register_term_cache` / `create_term_caches`) mirrors the ontology-service
  registry; `make_lifespan` builds one cache per ontology in play.

### Cached ontology sources (`search_api/services/ontology/cached.py`)

For small, flat ontologies (e.g. SEND) where caching the whole thing is cheap, unlike the
per-field incremental cache above:

- `CachedOntologyConcept` — `concept_id, preferred_term, synonyms, parent_ids` (a concept can have
  more than one parent). `CachedOntology` — one release of a concept table: `version, sha256,
  concepts`, where `version` is that source's own freshness signal (whatever ordering it naturally
  has — for SEND, a release/modified date) and `sha256` hashes the raw content fetched (an
  exact-equality signal, independent of what `version` means).
- `CachedOntologySource` (ABC) — one method, `fetch() -> CachedOntology`.
  `CachedOntologyStore` (ABC) — `read() -> CachedOntology | None` / `write(CachedOntology)`.
- `CachedOntologyService` — `OntologyService` fed by a `BootstrapCachedOntologySource`; fetches once
  at startup (`init`) and serves lookups from that in-memory table. No automatic background refresh;
  a newer release only takes effect on the next process start. Its `_find_concept_ids` hook
  matches a concept id, preferred term or synonym via `normalise_term`, resolving to every concept
  carrying that value and then keeping only those permitted by the field's `ontologyRestriction`
  (in SEND ~260 terms are shared between code lists, so the restriction is what disambiguates
  them); `_find_descendant_ids` walks `parent_ids` to any depth.
- `PostgresOntologyStore` — persists one ontology's full concept table as a single JSON snapshot in
  the shared `ontology_cache` table (`ontology_id, version, sha256, data, updated_at`), one row per
  `ontology_id`; `write` always replaces the entire stored snapshot (never per-concept updates).
- `BootstrapCachedOntologySource` — prefers whatever `PostgresOntologyStore` already has; fetches live
  and persists only the first time, when nothing is stored yet. Deliberate updates after that are
  expected to come from `scripts/admin.py`'s `send refresh` command, not from automatic re-fetching:
  it fetches live, skips entirely if the new version isn't newer than what's stored, and otherwise
  writes — bumping `version` even if `sha256` is unchanged (a republish with no real content
  change), or replacing the data too when `sha256` differs.

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

- `specimen`, `staining`, `diagnosis` and `finding` are **nested** types, each with a
  multivalued `qualifiers` keyword field
- All code/keyword fields are `keyword` (support array values natively)
- `age_at_extraction` is `integer_range` — stored as `{gte: <days>, lte: <days>}` (1 year = 365
  days, 1 month = 30 days; invalid input logged and dropped)
- `dataset_title/description/short_name` use the `english_text` analyzer

### Configuration (`conf.py`)

Settings come from the environment (pydantic-settings); most fields are **required** (no hardcoded
host/db/password defaults). Defaults that exist: `POSTGRES_PORT=5432`, `OPENSEARCH_PORT=9200`,
`DEPLOYMENT_ENV=dev`, `SNOMED_CACHE_REFRESH=300`, `FEATURE_AI=false`, `ADMIN_KEY=None` (admin
endpoints unmounted when unset), plus `OIDC_SCOPE`, `OIDC_SECURE_COOKIE=true`, `JWT_ISSUER` and
`JWT_ALGORITHM=HS256`. `DEPLOYMENT_TYPE`, `SNOWSTORM_URL`, `LLM_BASE_URL`/`LLM_API_KEY`, the
`OIDC_*` client settings and `JWT_KEY` (base64, must decode to ≥32 bytes) have no defaults.
A working set is in `tests/integration/.env`.

## Tests

```
tests/            # mirrors the search_api/ package layout
├── unit/          # run by tox; no external services needed
│   ├── api/{admin,auth,beacon,bigpicture,opensearch}/
│   ├── services/{ontology/,test_auth.py,test_session.py,test_validate.py}
│   └── utils/                 # crypt, dir, xml
├── integration/   # require Postgres/OpenSearch (route tests hit a running server)
│   ├── api/bigpicture/        # endpoints incl. AI (test_routes_ai.py, @skip — needs Ollama),
│   │                          # plus extract + load against Postgres
│   ├── database/, scripts/bigpicture/
│   └── services/{ontology/,test_sync.py}   # SNOMED hits a live Snowstorm
├── performance/   # locust load tests
├── utils/         # test helpers (generate_data.py)
└── files/bigpicture/xml/dataset_{clinical,non_clinical}/METADATA/   # XML fixtures
```

Integration `conftest.py` loads `tests/integration/.env` and provides module-scoped fixtures:
`bp_opensearch_docs` (override to supply inline documents), `bp_opensearch_index_name` (returns a
`bp-image-index-test-<uuid>` so each run is isolated), and `bp_opensearch_index` (creates, loads,
tears down). Test modules override `bp_opensearch_docs` with inline data — no external JSON file needed.
