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
# --build is required: without it, `up` reuses the existing cscfi/sd-search-api
# image and the server runs stale code.
docker compose --env-file tests/integration/.env --profile dev up --build

# Admin CLI. The first positional is the deployment to act on: every command under it
# acts on that deployment's own stores, its ontology caches included, since each
# deployment has its own database. `snomed` sits beside the deployments instead, since
# one Snowstorm is shared by all of them.
uv run python scripts/admin.py Bigpicture load <dir> --sync   # --dry-run parses only
uv run python scripts/admin.py Bigpicture sync                # only the documents pending sync
uv run python scripts/admin.py Bigpicture index generate      # writes the mapping file, touches no cluster
uv run python scripts/admin.py Bigpicture index create        # once per environment
uv run python scripts/admin.py Bigpicture index recreate      # index only; marks documents pending, so follow with sync
# A mapping change needs the index dropped, since OpenSearch cannot alter an existing
# field's type: `index recreate` rebuilds the index alone, `recreate` both stores.
# Refused when DEPLOYMENT_ENV=prod, as are `clear` and `index recreate`.
uv run python scripts/admin.py Bigpicture recreate
# `<deployment> refresh <id>` refreshes the preferred terms that deployment caches,
# updating the ontology from its source first if the database caches it whole
# (SEND does, SNOMED does not — Snowstorm serves it).
uv run python scripts/admin.py Bigpicture refresh snomed
uv run python scripts/admin.py Bigpicture refresh send
# Importing a SNOMED release writes to the shared Snowstorm and to no deployment. Takes
# hours, so it is its own command rather than part of a refresh.
uv run python scripts/admin.py snomed import --release-file <path/to/SnomedCT_*.zip>
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
│       ├── domain.py models.py ai.py opensearch.py
│       ├── extract/    # XML in, one document per image out:
│       │               # models.py refs.py values.py document.py
│       ├── config/     # hand-edited fields/groups/scopes YAML
│       ├── index/      # GENERATED OpenSearch mapping (`index generate` writes it)
│       └── schemas/    # XSDs used to validate the ingested XML
├── services/           # generic services
│   ├── ontology/       # service.py registrations.py snomed.py send.py term_cache.py
│   │                   # values.py — a document's coded values, made into concept ids
│   │   └── cache/      # one whole small ontology in memory:
│   │                   # models.py source.py store.py service.py
│   ├── auth.py session.py
│   └── load.py sync.py poller.py value_counts.py validate.py
├── database/           # every line of SQL, one module per table:
│   │                   # repository.py (connection) models.py (rows)
│   │                   # document.py document_log.py terms_cache.py ontology_cache.py
│   └── schema/         # create.sql drop.sql
├── utils/              # stateless helpers: crypt.py dir.py xml.py
├── ai/  conf.py  exceptions.py  main.py
```

**All Postgres lives in `database/`**, and nothing else does. A module there holds one table's SQL as
plain functions and knows nothing of ontologies or documents. Where a service needs to be stubbed in
tests, the service layer declares a `Protocol` and the concrete class satisfies it structurally — so
`database/` never imports from `services/`, and there is no ABC pretending a second backend exists.

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
beacon_service_factory                         # one shared service: health, status, value counts
query_endpoints: Sequence[BeaconQueryEndpoint] # /datasets, /images: service, response shape, AI persona
replace_concepts = True                        # substitute a retired concept at load
```

`/datasets` and `/images` are Bigpicture's two Beacon entity endpoints (see *Query path*), declared
as a `BeaconQueryEndpoint` each: its `path`, a `beacon_service_factory` building this request's
`BeaconQueryService` from a search client, the `result_sets_response_model` its records come back
as, and — read only when `FEATURE_AI` is set — its own `ai_assistant_description` /
`ai_result_model` / `ai_result_instructions`, mounted at `/ai<path>` (see *AI search*). Each needs
its own service (a different result shape, so a different `S` type parameter) and its own result
shapes, which is why `Domain` carries a sequence of endpoints rather than one pair of
factory/response-model fields — a deployment with a third entity just adds a third endpoint.
`Domain.beacon_service_factory` is separate: the one shared, query-less service behind `/status`,
`/health`, `/values`, `/suggestions` and `ValueCountsUpdater`, none of which depend on how a query
endpoint's records are shaped (see *BeaconService* below).

- `Domain.ontology_ids` → distinct `ontology.id`s referenced by the filtering terms.
- `make_lifespan` builds **one term cache per ontology** via `create_term_caches(domain.ontology_ids)`
  and stores them as `app.state.ontology_term_services: dict[ontology_id, OntologyTermCache]`.
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

2. **OpenSearch** (`api/opensearch/index.py`) — the search index (`bp-image-index` for Bigpicture).
   `SyncService` (`services/sync.py`, constructed with the index name) reads unsynced rows, bulk-indexes
   via `index_documents`, then stamps `synced_at`.

### Database connections (`database/repository.py`)

`get_cursor()` / `get_connection()` are how everything reaches Postgres. **The server pools its
connections; nothing else does.** `make_lifespan` calls `open_pool` before the caches that poll the
database start, and `close_pool` after they stop; while no pool is open `get_connection` connects
directly, which is what the admin CLI and the tests get. A load or sync holds one connection for its
whole run, so a pool would save it nothing and leave background workers to shut down. Measured on
localhost, a query costs ~6 ms unpooled against ~0.7 ms pooled, connecting being the difference.

The pool is a `psycopg_pool.AsyncConnectionPool` sized by `POSTGRES_POOL_MIN_SIZE` /
`POSTGRES_POOL_MAX_SIZE`, with three things set deliberately:

- **`check=AsyncConnectionPool.check_connection`** — every connection is probed on the way out of
  the pool, so one the server closed while it sat idle (a restart, an idle timeout, a dropped
  network) is discarded and replaced instead of failing the query that got it. The probe is one
  round trip, replacing the several that connecting costs. Without it a terminated backend surfaces
  as `AdminShutdown` to whichever request is handed it.
- **`timeout=POSTGRES_POOL_TIMEOUT`** (5 s, against psycopg_pool's 30) — how long a caller waits
  when every connection is in use, after which it raises `PoolTimeout`. That bound is what
  `tests/integration/database/test_repository.py` covers: hold `POSTGRES_POOL_MAX_SIZE` connections,
  each answering a `SELECT 1`, and the next one times out rather than opening a connection the
  database would eventually refuse.
- **`await pool.open(wait=False)`** — a database that is not up must not stop the server from
  starting, since `/health` is what reports that. The pool fills in the background and a query made
  before it does raises exactly as it would with no pool.

`POSTGRES_POOL_MAX_LIFETIME` replaces a connection once it reaches that age even while it is
working, so a database restarted or reconfigured since is reconnected to rather than only after
something breaks.

The OpenSearch-shaped payload is produced at **load** time by `build_document(document)`
(`api/opensearch/document.py`), which converts each `OpenSearchFieldValue`; `age_at_extraction`
ISO-8601 duration tuples become `{gte, lte}` day ranges via `iso8601_duration_to_days`.

`ExtractedDocument` separates the two kinds of value it carries: **`values`** are the top-level
fields, and **`groups`** holds one `OpenSearchGroup` per item of a nested group — the group's name
and its own values. Membership is what ties an item's values together, so nothing correlates them
positionally. `OpenSearchGroup` rejects a value whose `field.group` is not its own, so a misfiled
value is an error rather than a misplaced document. `all_values` walks both for the callers that
want every value regardless of where it sits.

A field's indexed path is exactly `<group>.<id>`, and `OpenSearchField` rejects a dot in either part:
a group nests one level and holds no group of its own, so a second level would be a mapping the
document builder cannot write and a nested query cannot reach.

Beyond those, `ExtractedDocument` carries `id`, `modified_at` and **`scope`**. Scope is not a
filtering term — it partitions documents rather than being searched — so it lives on the document, is
indexed at the root under `SCOPE_FIELD` (`api/scopes.py`), and `Domain` contributes that field to
`opensearch_fields` whenever the deployment declares any scope.

`LoadService.validate_document` is the boundary where a deployment's extraction meets the generic
store: it checks `scope` against `filtering_scopes`, so each extractor does not restate the declared
configuration.

### Document log (`search_api/database/document_log.py`)

A load records what it could not make sense of in the `document_log` table (`document_id`,
optional `field_id`, `severity`, `message`, `created_at`), keyed to the document it is about.
`severity` is `WARNING` or `ERROR`, constrained in the schema and by `LogSeverity`
(`severity.py`, its own module so `api/` and `database/` share the type without either importing
the other), and `write_document_log` (`database/document_log.py`) both inserts the row and emits it
through `logging` at the matching level, so a problem cannot be in one and not the other. The
message names only what the columns do not — the value and the ontology — and reads the same for
every occurrence, so rows group by it.

**Loading a document again replaces its rows** (`delete_document_logs` before the first is written),
so the table says what is wrong with the document as it stands rather than what was wrong with every
version of it: a problem corrected in the source disappears on the next load instead of sitting
there indistinguishable from a current one. A document skipped as not newer keeps the rows it has.

**Extraction reaches no database**, so what it cannot make sense of rides out on
`ExtractedDocument.logs` as `ExtractLog`s (`api/extract_logs.py`: `severity`, `field_id`, `message`)
and `extraction_logs` (`services/load.py`) turns each into a `document_log` row. That keeps
`load --dry-run` independent of the database, and it reports the same messages rather than writing
them, which makes a dry run a validation pass. Every `ExtractLog` is built by a named function in
`api/extract_logs.py` (`invalid_scheme_log`, `invalid_duration_log`) rather than by a deployment's
extractor, so the same problem reads the same whichever deployment found it.

A message names the **XML tag** only where the tag is not the field id, since the field id is
already a column. Bigpicture declares those cases in one table, `_XML_TAGS` — the five SEND
findings (`finding_severity` from `MISEV`) and `staining_substance` from `staining_compound` — so
every extraction call passes the **field id alone** and `_xml_tag` supplies the tag for the xpath.
Passing the tag instead is what made a log name a field that does not exist.

A deployment attributes its logs itself: Bigpicture pairs each parsed XML object with its own logs
(`_Extracted`) and extends the images that object is part of, so a statement's dropped code reaches
those documents and no others.

A retired concept is substituted before the document is stored: `LoadService._substitute_replaced_concepts`
asks the ontology for a replacement, indexes that instead, and logs the swap as a `WARNING`.
`Domain.replace_concepts` (default `True`) turns it off for a deployment that must index its source
unchanged. Nothing is logged then, and nothing is lost from the facet: SNOMED resolves a retired
concept to its own preferred term as readily as an active one, so what substitution buys is reach —
the subtree searches a retired concept falls out of — not a name. Only a single still-active `SAME_AS` or `REPLACED_BY` target counts — `POSSIBLY_EQUIVALENT_TO` is explicitly
uncertain, and several targets are a judgement rather than a substitution. This matters because
retiring a concept strips its relationships: a retired concept descends from nothing, so no subtree
query reaches a document citing one, whatever `activeFilter` is set to (measured: an ECL descendant
count is identical either way).

A value whose code is no concept id is resolved through its meaning before the document is stored (see
*Ontology providers*): one concept is a `WARNING` naming the meaning and the id indexed for it, while
several is an `ERROR` naming the candidates, and none — or no meaning at all — an `ERROR` saying which.
After an `ERROR` a strict `ontology` field drops the value, so the row is the only record of it.

What it currently records: every value of a strict `ontology` field that reached no preferred term.
Such a value **is** indexed, but `/filtering_terms/{field_id}/values` builds its response from the
resolved terms, so the value is missing from the facet and nothing can search for it by name — the
silent failure the table exists to make visible. An `ontologyOrValue` field is exempt, since an
unresolvable value there is indexed as free text by design. The check is against the term cache
rather than the resolution call, because the two provider kinds fail differently: a cached ontology
rejects an unknown id in `is_concept_id` before resolution is attempted, while SNOMED accepts any
well-formed id and fails to resolve it later.

The rows are about documents, so `admin.py <deployment> clear` deletes them with the documents.

### XML ingestion (`search_api/api/bigpicture/extract/`)

Four modules, each a step of the same job:

- **`models.py`** — the parsing models every other module is built on (`Bigpicture*Fields`,
  `BigpictureCodeAttributeValue`), an object's `ObjectIds`, and `BigpictureExtractedObject`, which
  carries one parsed XML object: its `ids`, its `fields`, and the `logs` of what was dropped from it.
- **`refs.py`** — the ref graph of one dataset. Every `map_ref` writes into a `BigpictureReferences`,
  whose `image_ids_from_*` methods follow the maps from an object to the images it reaches, which are
  the documents anything read from it belongs to.
- **`values.py`** — reading one element's values, and dropping those no ontology accepts.
- **`document.py`** — the orchestration: read the files, add every object to the images it reaches,
  and `to_opensearch_values` per document.

`tests/unit/api/bigpicture/extract/` mirrors `refs.py`, `values.py` and `document.py`, one test
module each. `models.py` has none: its only cross-checked invariant (`observation_type`'s
`controlledValues` and `group` matching `fields.yaml`) is a load-time `SystemException` check in
`bigpicture/models.py` instead, so it is verified on every import rather than only when tests run.

**A name is public exactly when another module in the package uses it.** `values.py` exposes its seven
`extract_*` functions and keeps its 21 readers and tables private; `refs.py` exposes
`BigpictureReferences`, `related_ids`, `object_ids`, `object_keys`, `map_ref` and the observation refs.
An underscore therefore means *module*-local, and `__init__.py`'s `__all__` is what says which of the
public names callers outside the package get.

`extract_documents(root, fs, single_dir, c4gh_private_key_file, c4gh_passphrase) → Iterator[ExtractedDocument]`
walks a directory tree and reads six XML files per dataset:

| File | Extracts |
|---|---|
| `METADATA/dataset.xml` | dataset ID, title, description |
| `METADATA/image.xml` | image IDs, slide mappings |
| `METADATA/policy.xml` | `scope` (see below) |
| `METADATA/sample.xml` | biological beings, cases, specimens, blocks |
| `METADATA/staining.xml` | staining procedures, substances, targets |
| `METADATA/observation.xml` | the `observation` group: diagnoses and findings, each with its own `observation_type` (optional file) |

`scope` comes from the policy's `type_of_dataset` attribute, whose value is
`"<scope>/<de-identification>"` (`Clinical/Anonymized`, `Clinical/Pseudonymized`,
`Non-Clinical/Obscured`, `Non-Clinical/Cryptonymized`). Only the part before the `/` is read, so a
new de-identification method needs no code change; that part is matched case-insensitively, while
the tag itself is not (it is the spec's machine name, read via `_extract_string_attribute_value`).
`_extract_scope` raises `UserException` if the attribute is missing or its scope is not
`Clinical`/`Non-Clinical`.

It builds ID-chain mappings (image→slide→block→specimen→case→biological being), parses into the
Bigpicture models below, then `to_opensearch_values(fields)` converts them to the document's
top-level `OpenSearchFieldValue`s and one `OpenSearchGroup` per nested item, keyed by the fields
declared in `BP_DOCUMENT_FIELDS`. An item contributing no indexable value is not indexed at all. `age_at_extraction`
is an ISO-8601 duration tuple `(start, end)` — e.g. `("P40Y", "P41Y")` — computed by
`_extract_iso8601_duration` (uses `isodate`, normalises month overflow); an invalid duration is dropped and
recorded as an error against the document. `.c4gh`-encrypted XML is decrypted on the fly (`utils/crypt.py`).

The parsing models are in `extract/models.py`; `BigpictureFields` is the per-image root, holding the
ids, `scope`, the dataset fields, and the `specimen`, `staining` and `observation` sets.
`BigpictureSpecimenFields` flattens the biological being, specimen and block fields into one model
(see the grouping rationale in `fields.yaml`); `BigpictureObservationFields` similarly flattens a
diagnosis and a finding statement into one model, since a document's scope makes the two mutually
exclusive (see below).

Every `CODE_ATTRIBUTE` contributes the pair `(CODE, MEANING)` as its value, the meaning being what the
load falls back to when the code is no concept id (see *Ontology providers*).
`_filter_value_by_scheme` / `_filter_values_by_scheme` drop a value whose scheme is not the field's
ontology — recording an error against the document, since nothing downstream can see what was dropped
— because its code is no concept id of the one required however much it may look like one.

An **`ontologyOrValue`** field takes its coded value in precedence over free text: `<id>_other` is
filled only when no code was read, since free text is what a source gives for a value it has no code
for, rather than a label for one it has. `_extract_ontology_or_value` is that rule for
`staining_procedure` and `staining_substance`; `_extract_fixation_type` reaches the same end by its own
route, because a scheme of `Other` (`_UNCODED_SCHEME`) declares the coded value itself uncoded and
routes its text to `fixation_type_other`.

`scope` is not in `fields.yaml` — it is `ExtractedDocument.scope`, produced by `extract_scope`.

An observation statement contributes a diagnosis or a finding to the `observation` set depending on
its `STATEMENT_TYPE`; the parsing is otherwise identical, including how statements reach images. Both
carry `observation_type` (`confirmed` / `candidate`, constants in `extract/models.py`): a statement
linked by `IMAGE_REF`, or whose `STATEMENT_STATUS` is `Distinct`, is `confirmed` for the image, and
anything reaching several images through another ref is a `candidate` for each. Each statement yields
**one nested item**, carrying the single `observation_type` value that statement was made under; items
are not merged across statements. The group is a `set`, so a statement repeated verbatim for an image
collapses. The same code stated `confirmed` by one statement and `candidate` by another therefore
appears as two items — which no consumer can see, since facet counts are document counts.

Because `specimen`, `staining` and `observation` are held in `set`s their models must be hashable —
hence `frozen=True`, and `frozenset` for `anatomical_site`. The `@field_serializer` on
`anatomical_site` serialises it as `list[dict]` (Pydantic's default `set[dict]` is unhashable and not
JSON-serialisable).

### Configurable fields (YAML)

Indexed fields and filtering terms are declared in `api/bigpicture/config/fields.yaml` and loaded by
`api/fields.py` (`load_fields_config`) into `BP_DOCUMENT_FIELDS` / `BP_FILTERING_TERMS`
(`api/bigpicture/models.py`). The OpenSearch index mapping JSON is generated from these fields by
`OpenSearchIndexGeneratorService` (`api/opensearch/index_generator.py`) via the
`index generate` admin command.

An `ontology` / `ontologyOrValue` field may declare an `ontologyRestriction` — the part of the
ontology its values may resolve to. It is deployment configuration, excluded from both API
responses and the OpenAPI schema. It is optional: a field without one resolves against the whole
ontology (`snomed_ecl` is then `None`), which is why the provider hooks verify their match.

```yaml
ontologyRestriction:
  concept_ids: [ "410607006" ]   # Organism
  include_descendants: true      # the subtree; false means exactly these concepts
```

### API layer & routes

The Beacon router is generic, built per domain by `make_beacon_router(domain)`
(`api/beacon/routes.py`). Dependency providers (`get_beacon_service`, `get_beacon_query_services`,
`get_ontology_term_services`) resolve services from `app.state` and are module-level so tests can
override them via `app.dependency_overrides`. `get_beacon_service` returns
`domain.beacon_service_factory`'s one shared instance, used by every route that only needs the
generic `BeaconService` behaviour (health, status, value counts) — none of those depend on how a
query endpoint's records are shaped, so `make_lifespan`'s `ValueCountsUpdater` (which needs one
persistent instance) does not have to know Bigpicture calls its endpoints `/datasets` and
`/images`. `get_beacon_query_services` builds this request's `BeaconQueryService` for every endpoint
`domain.query_endpoints` declares, keyed by path — cheap, since building one wraps a search client
and config, no I/O — and is what `/datasets`, `/images`, and their `/ai/*` counterparts each read
their own service from by path.

| Endpoint | Description |
|---|---|
| `GET /info` | Beacon metadata |
| `GET /filtering_terms` | Available filter definitions |
| `GET /filtering_groups` | UI groupings of filtering terms |
| `GET /filtering_scopes` | Available scopes (e.g. `clinical` / `non_clinical`) |
| `POST /datasets` | Beacon V2 search, images aggregated into their datasets |
| `POST /images` | Beacon V2 search, one result per matching image |
| `POST /ai/datasets` | Natural-language dataset search (gated by `FEATURE_AI`) |
| `POST /ai/images` | Natural-language image search (gated by `FEATURE_AI`) |
| `GET /filtering_terms/{field_id}/values` | Indexed values with counts; ontology fields resolve concept IDs to preferred terms |
| `GET /filtering_terms/{field_id}/suggestions` | Autocomplete restricted to indexed values |
| `GET /status` | What the deployment holds: documents indexed and pending, in total and per scope, and when a document was last synced |
| `GET /health` | Both stores answer; a `503` names the one that did not |

`AuthMiddleware` (`api/middlewares.py`) requires a session on every route outside `PUBLIC_PATHS`,
which is why `/health` and `/info` answer anonymously while `/status` — the same kind of operational
report, but one that says how much data a deployment holds — is a `401` without one. `/admin` is
public to the middleware and gated by `ADMIN_KEY` instead.

`/status` reads its pending counts and last-synced time from Postgres and its indexed counts from
OpenSearch, one `count` per scope plus one for the total. An index that does not exist counts zero
rather than erroring, so the endpoint still answers before `index create` has been run. Every count
is a term lookup, so nothing here scales with the number of documents — except the pending
counts, which read one row per document still awaiting sync.

`values` and `suggestions` both accept `scope=<id>` and restrict their counts by it. Omitting it
does not filter on it — there is no default. A scope the field does not declare is a `400` rather
than an empty list, since the field is never indexed for those documents (`validate_field_scope`).

Admin routes (`api/admin/routes.py`, `/admin` prefix, mounted only when `ADMIN_KEY` set, SNOMED-specific):
`/admin/snomed/reload`, `/admin/snomed/refresh`, `/admin/snomed/fields/{field_id}/invalid_concepts`,
`/admin/snomed/fields/{field_id}/unexpected_concepts`.

Auth routes (`api/auth/routes.py`, always mounted): `GET /login`, `GET /callback`, `GET /logout` —
an OIDC relying party (`services/auth.py`) that issues a session JWT cookie. Configured by
`OIDCConfiguration` / `JWTConfiguration` in `conf.py`.

### BeaconService (`api/beacon/services.py`, `api/opensearch/beacon.py`)

`BeaconService` (ABC, `Generic[T]`) covers a deployment's health, indexed counts and field value
counts — nothing that depends on how a query endpoint's records are shaped, so **one instance
answers for the whole deployment**. `BeaconQueryService` (ABC, `Generic[S]`) is the separate,
independent contract for the one thing that does depend on that shape: `query`, returning
`BeaconQueryResult[S]` — `total` (the match count) and `result_sets` (the records, for record
granularity) kept apart, so a count can be served without materialising every match. It does not
inherit `BeaconService` — nothing ever calls `count_indexed`/`is_healthy`/`get_value_counts` on a
value typed as `BeaconQueryService` (the query route and the AI agent's query tool only ever call
`query`), so forcing that capability onto the abstract type would be unused surface, not a real
dependency. A class needing both inherits both, as `OpenSearchQueryBeaconService` does. A query
endpoint (`/datasets`, `/images`) needs its own `BeaconQueryService`; every other route needs only
the query-less `BeaconService`.

`OpenSearchBeaconService(BeaconService[OpenSearchBeaconFilteringTerm])` (`api/opensearch/beacon.py` —
everything OpenSearch-specific lives under `api/opensearch/`, `api/beacon/services.py` holds only the
backend-agnostic contracts) implements that query-less contract against OpenSearch (field value
counts, cluster health) and is **concrete on its own** —
no deployment needs to subclass it just to answer `/status`/`/health`/`/values`/`/suggestions`;
Bigpicture's `Domain.beacon_service_factory` builds one directly.
`OpenSearchQueryBeaconService(OpenSearchBeaconService, BeaconQueryService[S])` adds query
construction and boolean/count/record dispatch on top of it — the `OpenSearchBeaconService` side
supplies `client`/`index_name`/`filtering_scopes` for genuine code reuse, unrelated to what
`BeaconQueryService` itself declares. Bigpicture provides two concrete
query services (`api/bigpicture/opensearch.py`), one per entity endpoint:
`BigpictureDatasetBeaconService` (`OpenSearchQueryBeaconService[BigpictureBeaconDatasetResult]`)
groups images into datasets; `BigpictureImageBeaconService`
(`OpenSearchQueryBeaconService[BigpictureBeaconImageResult]`) does not. Both implement only the two
abstract methods the base class leaves open, `_get_count` and `_get_records`.

### Query path

`/datasets` and `/images` (`api/beacon/routes.py`) are registered by the same
`register_query_route(endpoint)`, called once per `domain.query_endpoints` entry inside
`make_beacon_router`: the two run the exact same handler and differ only in which
`BeaconQueryService` answers the query (looked up from `get_beacon_query_services()`'s dict by the
endpoint's own path) and which response model wraps the result — both are the `BeaconQueryEndpoint`
itself, not the router's concern. Each receives a `BeaconQueryRequest`. Ontology filters (`ontology` / `ontologyOrValue`)
are resolved first: per filter, `get_ontology_service(term.ontology.id).prepare_ontology_filter(...)`
turns free text / concept IDs into concept IDs (optionally expanding to descendants when
`includeDescendantTerms`), scoped by the field's `ontologyRestriction` — see the resolution cascade
under *Ontology providers*. The filter `type` (`BeaconFilteringTermType`) then selects the query
builder (`api/opensearch/clauses.py`):

| type | builder | notes |
|---|---|---|
| `text` | `build_match_clause` | full-text match |
| `controlledValue` / `keyword` | `build_term_clause` / `build_terms_clause` | exact keyword match |
| `ontology` / `ontologyOrValue` | `build_term_clause` | exact concept-ID match |
| `iso8601Range` | `build_iso8601_range_clause` | ISO-8601 duration range, converted to days |

**A filter only constrains the scopes its field is indexed for.** A field absent from a scope cannot
match any document of it, so including the filter as a plain condition would exclude every such
document rather than leave it alone. When some filter is scope-specific, `_get_query_clause` therefore emits
one `should` branch per scope — each pairing that scope with only the filters that apply to it — and a
document matches through its own scope's branch. With every filter applying to every scope in play the
branches reduce to one flat query, which is what is emitted instead. The corollary: filtering on
`diagnosis` (clinical-only) returns non-clinical documents untouched, and filtering on a field with
`requestedScope` set to a scope it does not cover leaves that scope unconstrained.

Filters mapping to multiple OpenSearch fields are combined with `build_or_clause`; filters on a nested
group (`specimen`, `staining`, `observation`) are wrapped in nested queries — every filter on fields
of the same group lands in **one** nested query, so it must hold for the very item that matched
rather than for any item in the group. Filtering `diagnosis` and `observation_type` together, for
example, only matches an image whose *same* observation item carries both.

**`_get_count(query_clause)` and `_get_records(query_clause)`** are the two seams left abstract by
`OpenSearchQueryBeaconService`: given the finished query clause, answer a count or list every
record. Each returns its own natural type — `int`, `BeaconResultSets[S]` — rather than a shared
wrapper, so neither fakes a total or a record it does not have. `query()` on the base class is the
only place that builds a `BeaconQueryResult`: it calls `_get_count` for count granularity and wraps
the int with an empty `result_sets`, or calls `_get_records` for record granularity and sets
`total` from `len(result_sets.resultSet)`. Boolean granularity bypasses both — `_get_boolean_result`
reads `hits.total.value` off a `{"size": 0}` search directly and passes it through as `total`
unclamped, since boolean only ever reads it as `> 0` and the real number costs nothing extra.

Both Bigpicture services page their OpenSearch response via a shared, deployment-agnostic mechanism
in `api/opensearch/search.py` (composite aggregation and plain search alike): `iter_paged_buckets`
and `iter_paged_documents` are plain async generators that know how to fetch and page — a page
shorter than `page_size` is the last one, so no confirming empty page is fetched afterward — and
yield raw buckets or raw `_source` dicts. Neither takes a callback; there is no `build_record` or
similar crossing a function boundary. What a bucket or hit *means* is entirely Bigpicture's own,
written as an ordinary loop in `api/bigpicture/opensearch.py` — deliberately, since a caller-supplied
callback threaded through a generic pager was hard to trace (the request shape, the response
handling, and the cursor's lifecycle were split across three functions read out of order); a plain
loop the caller owns keeps all three in one place, at the cost of the two Bigpicture services each
writing their own accumulation loop instead of sharing one.

`BigpictureDatasetBeaconService._get_records` iterates `iter_paged_buckets` with `dataset_id` and
`image_id` as composite sources, so each bucket is one image within a dataset. Its loop builds a
`BigpictureBeaconDatasetResult` the first time a `dataset_id` is seen (`_build_dataset`, reading
title/description via `top_hits_source(bucket, name)` — the response-side counterpart of
`top_hits_sub_agg`, since a composite key alone doesn't carry them) and folds every bucket's
`image_id` into it. `_get_count` iterates the same generator with `dataset_id` alone as the only
source and no `sub_aggs` — cheaper than `_get_records`, since counting distinct dataset ids needs
no title/description fetch at all.

`BigpictureImageBeaconService` has nothing to group — a document already is one image — so
`_get_count` is a single `count_documents` call, and `_get_records` is a list comprehension over
`iter_paged_documents` (`source_fields=["image_id"], sort_field="image_id"`), building one
`BigpictureBeaconImageResult` per `_source` directly.

`get_value_counts` serves `ValueCounts` from a plain dict on the service, keyed by
`ValueCountsKey` (`api/models.py`) — the field and the scope, frozen so it can key a dict.
Nothing expires; `ValueCountsUpdater` (`services/value_counts.py`) owns what is in there, clearing
it and refilling when `max(document.synced_at)` moves, polled every `VALUE_COUNT_CACHE_REFRESH`
seconds. Every key is counted concurrently, each in its own task so one failing does not stop the
rest. The requests are enumerable — every valued field, against its own scopes, which is 41 for
Bigpicture — so `_value_count_keys()` yields them as keys, derived from the deployment's config
rather than guessing what a client will ask for. A request it did not anticipate is counted on the
way through and kept, so only the first one pays for it. Nothing is filled until a document has been
synced: counting an index no load has reached would cache one empty answer per request.

`get_value_counts(field_id, scope)` applies the scope to the facet aggregation so counts match what
the equivalent query returns: `scope` becomes the aggregation's document `query`. With no scope
given, everything is counted.

**Counts are document counts.** A bucket inside a `nested` aggregation counts group items, so a
`reverse_nested` sub-aggregation (`documents`) climbs back to the documents holding them and its
count is the one returned. Without it an image with two `Female` specimens would count twice for
`Female`. Top-level fields need no reverse_nested — their buckets already count documents. Response granularity (`boolean` / `count` / `record`)
comes from `request.query.requestedGranularity`.

### Ontology providers (`search_api/services/ontology/service.py`)

`OntologyService` (ABC) abstracts term resolution for one ontology: `is_concept_id`,
`get_preferred_terms`, `prepare_ontology_filter`, and `replacement_concept_id` — the last one
concrete, returning `None`, so an ontology that retires nothing need not answer it. `prepare_ontology_filter` is a template
method implemented once on the ABC — filtering-term lookup, value normalisation, the
resolved/unresolved split, and the final filter rebuild are identical across providers. Each
provider only implements two hooks: `_find_concept_ids(value, filtering_term)` (one value ->
its concept ID(s), possibly more than one if a term isn't unique) and
`_find_descendant_ids(concept_ids)` (a set of concept IDs -> all of their descendants).

Each value is resolved by `resolve_concept_ids`, cheapest source first:

1. **A concept id is taken as given** — an id absent from the ontology is absent from the index
   too, so looking it up would cost a round trip without changing the result.
2. **A preferred term cached for the field** (`terms_cache`, matched via `normalise_term`, so case-
   and space-insensitively) resolves in memory. Unlike the field's `ontologyRestriction` this covers
   every concept actually indexed for the field, including ones outside the restriction.
3. Otherwise the provider's `_find_concept_ids` hook consults the ontology itself.

Unresolved values are only kept in the prepared filter for `ontologyOrValue` fields (which have a
free-text fallback field downstream); for strict `ontology` fields they're dropped.

**A load resolves its values through the same cascade** (`services/ontology/values.py`,
`resolve_document`),
so a value indexed and a value searched for reach the same concept. The code the source coded is tried
first: it is kept as it is when it is a concept id, and only when it is not does the **meaning**
carried beside it get resolved — the term is then the only thing left that can name the concept. That
the code was unusable is a `WARNING` when the meaning names one concept, and an `ERROR` when nothing
does: several matches are a judgement rather than a resolution, and a value with no meaning has
nothing to fall back to. This runs before the retired-concept substitution, so a meaning naming a
retired concept is resolved and then replaced.

An ontology value is therefore the pair `(concept id, meaning)`, which `_VALUE_TYPES` requires of the
`ontology` and `ontologyOrValue` types just as it requires a pair of `iso8601Range`. The concept id is
`None` when no ontology the field accepts coded the value, leaving the meaning all there is to resolve.
`OpenSearchFieldValue` is **frozen**, so nothing rewrites what a source said: resolving records the
concept id reached on a copy's `resolved_concept_id`, and `_encode_value` indexes that.

**A value that resolves to no concept id is dropped, whichever of the two types its field is.** Both
hold concept ids, so an unresolvable one is no more indexable in one than in the other, and a term left
in either would be searchable by nothing and would name nothing in the field's values; the
`document_log` rows are what keep what the source said. The difference between the types is elsewhere
entirely: an `ontologyOrValue` term has a second field of its own (`<id>_other`), which extraction
fills with the free text the source gave — `values.py` never touches it. `SnomedService` will not send Snowstorm a term under
`_MIN_SEARCH_TERM_LENGTH` (3) characters, which it answers with a `400`; `_fetch_concepts` is cached
for 30 days like the rest, so a load searches once per distinct term rather than once per document. A registry maps
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

`_fetch_concept` is the one call for reading a single concept whole, cached for 30 days and shared by
`replacement_concept_id` and `_describes`. It reads Snowstorm's **browser** view, because
`/{branch}/concepts/{id}` returns the concept's own columns alone — no `associationTargets`, no
`inactivationIndicator`, no `descriptions` (measured: the browser view costs 10.1 kB against the
3.5 kB of the descriptions alone for `84499006`, and 68.7 kB against 65.1 kB for `138875005`, where
the descriptions dominate either way). `_fetch_all_concepts` is cached for 30 days too. Set `SNOWSTORM_URL` to
enable.

`import_snomed_release(release_file, branch)` automates the README's "Import SNOMED release"
procedure: creates a Snowstorm import job, uploads the release archive, then polls
`/imports/{id}` until it reports `COMPLETED` (raising on `FAILED`; a `404` also means done, per
Snowstorm's own behaviour of dropping completed jobs). Invoked by `scripts/admin.py`'s
`snomed import --release-file <path>`, which writes to the shared Snowstorm alone; refreshing the
terms a deployment caches against it is that deployment's own `refresh snomed`.

### Reloading a cache (`search_api/services/poller.py`)

Every cache below is filled from a store another process writes — the admin CLI's load, sync or
ontology refresh — so none of them re-reads the data to find out. `UpdatedPoller` holds the one loop
they share: `start()` reads the store's `updated_at`, refreshes, then reloads whenever `updated_at`
moves. Three details it centralises:

- **`updated_at` is read before the refresh**, so a write landing during it is either included by that
  refresh or seen by the next poll. Read afterwards, a write in that window would be recorded as
  already loaded and missed.
- **`None` means nothing is stored**, so the first refresh is skipped — counting an index no load has
  reached would cache one empty answer per key.
- **It is recorded only after a refresh succeeds**, so a failed one is retried rather than skipped.

The value comes from the store, never from this process's clock: rows are stamped by the database,
whose clock can run behind ours. Only whether it changed matters, so a deletion moving it backwards
counts too. `start()` is therefore `async` on every cache, and it performs the initial load — callers
do not load separately.

### Ontology term cache (`search_api/services/ontology/term_cache.py`)

A persistent cache mapping `(ontology_id, field_id, concept_id)` → preferred term, served from an
in-memory index reloaded on a background interval. **Every ontology has one, whatever its size** — it
holds not the ontology but the part of it a field uses: the concepts observed while indexing, which is
what `/values` and `/suggestions` report and what a requested term resolves against. An ontology small
enough to cache whole still needs it, since the whole ontology does not say which concepts a field
carries.

`OntologyTermCache(ontology_id)` is one concrete class, not an interface: it owns the index and the
resolve-against-the-ontology orchestration of `cache_preferred_terms` / `refresh`, and reloads through
an `UpdatedPoller` over `read_updated_at` every `TERM_CACHE_REFRESH` seconds. Its store is
`database/terms_cache.py`
— `read_terms`, `read_concept_ids_by_field`, `read_updated_at`, `insert_terms`, `update_terms`, over a
`StoredTerm` model (`database/models.py`) rather than row tuples — so everything but the SQL is unit-testable without a
database. All ontologies share the one `terms_cache` table
(`ontology_id, concept_id, field_id, preferred_term, updated_at`) — shared across ontologies, not
across deployments: no table carries a deployment column, because each deployment has its own
database (`POSTGRES_DB`), so a second deployment caches its own copy of everything.

`create_term_caches(ontology_ids)` builds one cache per ontology id. There is no factory registry:
every ontology's cache is constructed the same way, so `make_lifespan` and the admin CLI just call it.

### Cached ontology sources (`search_api/services/ontology/`)

For small, flat ontologies (e.g. SEND) where caching the whole thing is cheap, so resolution needs no
terminology server. Orthogonal to the term cache above, which every ontology has:

- `CachedOntologyConcept` — `concept_id, preferred_term, synonyms, parent_ids` (a concept can have
  more than one parent). `CachedOntology` — one release of a concept table: `version, sha256,
  concepts`, where `version` is that source's own freshness signal (whatever ordering it naturally
  has — for SEND, a release/modified date) and `sha256` hashes the raw content fetched (an
  exact-equality signal, independent of what `version` means).
- `OntologySource` (ABC, `source.py`) — `fetch() -> CachedOntology` plus `is_newer`.
  `OntologyCacheStore` (`store.py`) — `read() -> CachedOntology | None` / `write(CachedOntology)` /
  `updated_at()`. The models are in `models.py`; there is no store interface, since one store is all
  there is and mypy checks the calls against it.
- `CachedOntologyService` (`cache/service.py`) — an `OntologyService` built from a store and a source.
  `init` serves what the store holds, fetching from the source and storing it only when nothing is
  stored yet, then serves lookups from that in-memory table. `start()` polls
  `OntologyCacheStore.updated_at` through an `UpdatedPoller` every `ONTOLOGY_CACHE_REFRESH` seconds,
  so a `refresh send` by the admin CLI reaches a running server without a restart. `updated_at` is
  read rather than the ontology itself, so an unchanged store costs one row. Its `_find_concept_ids` hook
  matches a concept id, preferred term or synonym via `normalise_term`, resolving to every concept
  carrying that value and then keeping only those permitted by the field's `ontologyRestriction`
  (in SEND ~260 terms are shared between code lists, so the restriction is what disambiguates
  them); `_find_descendant_ids` walks `parent_ids` to any depth.
- `OntologyCacheStore` — persists one ontology's full concept table as a single JSON snapshot in
  the shared `ontology_cache` table (`ontology_id, version, sha256, data, updated_at`), one row per
  `ontology_id`; `write` always replaces the entire stored snapshot (never per-concept updates). It
  maps rows to and from the models; the SQL is `database/ontology_cache.py`.
- Nothing re-fetches on its own after that first store. Deliberate updates come from
  `scripts/admin.py`'s `<deployment> refresh send`, which fetches live, skips entirely if the new version isn't
  newer than what's stored, and otherwise writes — bumping `version` even if `sha256` is unchanged
  (a republish with no real content change), or replacing the data too when `sha256` differs. A
  running server notices that write through the `updated_at` poll above.

### AI search (`search_api/ai/`)

`AIService` (`ai/services.py`) is a generic pydantic-ai agent (Ollama by default) over a
`BeaconQueryService` (its `query` tool calls `ctx.deps.query(...)`, so it needs one specific query
endpoint's service, never the query-less `BeaconService`). It's parameterised by `result_model`
(the agent's structured `output_type`) and `result_instructions` (step 3 of the system prompt). The
generic prompt covers the tool flow (`get_filtering_terms`, then the `query` tool) and a
scope-constraint block; deployments supply the persona and result shape per query endpoint, on its
`BeaconQueryEndpoint` (`ai_assistant_description`, `ai_result_model`, `ai_result_instructions`) —
the same reasoning as the endpoint's own `result_sets_response_model`: the AI agent's structured
output mirrors that endpoint's own record shape, so it travels with the endpoint rather than living
once on `Domain`.

`ai/models.py` holds the generic base `AISearchResult` (`interpretation`, `filters`) and
`AIQueryFilter`. Bigpicture's two result shapes and prompt fragments live in
`api/bigpicture/ai.py`: `BigpictureAIDatasetSearchResult` (`dataset_count` + nested `Dataset`) for
`/ai/datasets`, `BigpictureAIImageSearchResult` (`image_count` + nested `Image`) for `/ai/images` —
both share `BP_AI_ASSISTANT_DESCRIPTION`, since the persona does not need to differ by entity. The
agent's `output_type` constrains the LLM to emit JSON matching the model; `result.output` is the
concrete subclass.

`register_ai_query_route(endpoint)` (`api/beacon/routes.py`), called once per `domain.query_endpoints`
entry when `FEATURE_AI` is set, mounts one agent at `/ai<path>` — e.g. `/ai/datasets`, `/ai/images`.
The `AIService` is built once per endpoint at router-construction time, not per request: it holds no
per-request state, only the pydantic-ai `Agent`, which is meant to be built once and reused across
runs. The route itself reads its own service out of `get_beacon_query_services()`'s dict by
`endpoint.path`, the same lookup `register_query_route` uses.

### OpenSearch index mapping highlights

- `specimen`, `staining` and `observation` are **nested** types
- All code/keyword fields are `keyword` (support array values natively)
- `age_at_extraction` is `integer_range` — stored as `{gte: <days>, lte: <days>}` (1 year = 365
  days, 1 month = 30 days; invalid input logged and dropped)
- `dataset_title/description/short_name` use OpenSearch's built-in `english` analyzer, so the
  generated index needs no `settings` at all: it stems both the indexed text and the query, so
  `staining` finds "stained" and `cancer` finds "Cancers". Changing it requires a recreate and
  reload, since a field's analyzer is fixed at index creation. Tuning it (stem_exclusion, custom
  stopwords, synonyms) would mean declaring a named analyzer in the settings again.
- `build_match_clause` sets `minimum_should_match` to `2<75%`: up to two terms all must match, so the
  common two-word query behaves like `and`; beyond that three quarters must, tolerating one stray
  word. The `or` default would need only one term, which is far too broad given that results are
  never ranked (see *Query path*) — a document matching one word would be indistinguishable from one
  matching all of them.

### Configuration (`conf.py`)

Settings come from the environment (pydantic-settings); most fields are **required** (no hardcoded
host/db/password defaults). Defaults that exist: `POSTGRES_PORT=5432`,
`POSTGRES_POOL_MIN_SIZE=2`, `POSTGRES_POOL_MAX_SIZE=10`, `POSTGRES_POOL_MAX_LIFETIME=3600`,
`POSTGRES_POOL_TIMEOUT=5`, `OPENSEARCH_PORT=9200`,
`DEPLOYMENT_ENV=dev`, `TERM_CACHE_REFRESH=300`, `ONTOLOGY_CACHE_REFRESH=300`,
`VALUE_COUNT_CACHE_REFRESH=300`, `FEATURE_AI=false`, `ADMIN_KEY=None` (admin
endpoints unmounted when unset), plus `OIDC_SCOPE`, `OIDC_SECURE_COOKIE=true`, `JWT_ISSUER` and
`JWT_ALGORITHM=HS256`. `DEPLOYMENT_TYPE`, `SNOWSTORM_URL`, `LLM_BASE_URL`/`LLM_API_KEY`, the
`OIDC_*` client settings and `JWT_KEY` (base64, must decode to ≥32 bytes) have no defaults.
A working set is in `tests/integration/.env`.

## Tests

```
tests/            # mirrors the search_api/ package layout
├── unit/          # run by tox; no external services needed
│   ├── api/{admin,auth,beacon,bigpicture,opensearch}/
│   ├── services/{ontology/,test_auth.py,test_load.py,test_poller.py,
│   │              test_session.py,test_validate.py,test_value_counts.py}
│   └── utils/                 # crypt, dir, xml
├── integration/   # require Postgres/OpenSearch (route tests hit a running server)
│   ├── api/bigpicture/        # endpoints incl. AI (test_routes_ai.py, @skip — needs Ollama),
│   │                          # plus extract + load against Postgres
│   ├── database/              # one module per table, plus test_repository.py (the pool)
│   ├── scripts/               # test_admin.py (ontology updates) + bigpicture/test_admin.py
│   └── services/{ontology/,test_load.py,test_poller.py,test_sync.py}
├── performance/   # locust load tests
├── utils/         # test helpers (generate_data.py)
└── files/bigpicture/xml/dataset_{clinical,non_clinical}/METADATA/   # XML fixtures
```

A test needing a reachable Snowstorm carries `@pytest.mark.requires_snowstorm`, and
`SKIP_SNOWSTORM_TESTS=true` skips those — set in CI, which cannot reach the internal-only Snowstorm
that `tests/integration/.env` points at. The marker is registered and the skip applied in
`tests/integration/conftest.py`.

Integration `conftest.py` loads `tests/integration/.env` and provides module-scoped fixtures:
`bp_opensearch_docs` (override to supply inline documents), `bp_opensearch_index_name` (returns a
`bp-image-index-test-<uuid>` so each run is isolated), and `bp_opensearch_index` (creates, loads,
tears down). Test modules override `bp_opensearch_docs` with inline data — no external JSON file needed.
