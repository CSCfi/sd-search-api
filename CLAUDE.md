# CLAUDE.md

A **FastAPI** app implementing **Beacon V2** over Postgres + OpenSearch. It is **deployment-agnostic**: every
deployment-specific behaviour is captured in a `Domain`, and the generic machinery (router, lifespan, load/sync,
ontology resolution) is built from it. Bigpicture (pathology image search) is the only deployment so far.

## Commands

```bash
tox                                  # ruff format+lint, mypy, pytest (tests/unit/ only)
tox -e ruff | -e mypy | -e pytest
.venv/bin/pytest tests/unit/path/test_x.py::test_name -x

DEPLOYMENT_TYPE=Bigpicture uvicorn search_api.main:app --reload   # or: sd_search_api

# --build is required: without it `up` reuses the existing cscfi/sd-search-api image and the server runs stale code.
docker compose --env-file tests/integration/.env --profile dev up --build

# Admin CLI. The first positional is the deployment, and everything under it acts on that deployment's own stores
# (each has its own database); `snomed` sits beside the deployments, one Snowstorm being shared by all.
uv run python scripts/admin.py Bigpicture load <dir> --sync     # a directory
uv run python scripts/admin.py Bigpicture fetch --sync          # the submit API instead
uv run python scripts/admin.py Bigpicture sync                  # only documents pending sync
uv run python scripts/admin.py Bigpicture index generate        # writes the mapping file only
uv run python scripts/admin.py Bigpicture index create|recreate # recreate marks docs pending: sync after
uv run python scripts/admin.py Bigpicture clear|recreate        # docs+terms+logs+marker / both stores
uv run python scripts/admin.py Bigpicture refresh snomed|send   # cached preferred terms
uv run python scripts/admin.py snomed import --release-file <SnomedCT_*.zip>   # hours; shared Snowstorm
```

`load` and `fetch` are one loop over two sources: same flags, one shared resume point. Either loads only what
changed; `--full` forgets that and loads everything, `reset` forgets it without loading, `--dry-run` reads it and
writes nothing, so it is a validation pass. `load` infers whether `<dir>` is one dataset directory or a parent of
several, and takes the Crypt4GH key from configuration. `clear`, `recreate` and `index recreate` are refused in prod.

## Architecture

```
search_api/
├── api/
│   ├── {admin,auth,beacon,jwk,opensearch}/   # generic routers and services
│   └── bigpicture/     # everything Bigpicture-specific, nowhere else:
│       ├── domain.py models.py ai.py opensearch.py local.py remote.py   # its two DocumentSources
│       ├── extract/    # XML in, one document per image out: models.py refs.py values.py document.py
│       ├── config/     # hand-edited fields/scopes YAML
│       ├── index/      # GENERATED mapping (`index generate` writes it)
│       └── schemas/    # XSDs
├── services/
│   ├── ontology/       # service.py registrations.py snomed.py send.py term_cache.py values.py
│   │   └── cache/      # one whole small ontology in memory
│   ├── fetch.py        # DocumentSource (ABC) + SdSubmitFetchClient
│   └── auth.py session.py load.py sync.py poller.py value_counts.py validate.py
├── database/           # every line of SQL, one module per table: repository.py models.py document.py
│   │                   #   document_log.py terms_cache.py ontology_cache.py load.py
│   └── schema/         # create.sql drop.sql
├── utils/ (crypt dir token xml)  ai/  conf.py  exceptions.py  main.py
```

**All Postgres lives in `database/`**, and nothing else does; where a service needs stubbing it declares a
`Protocol` the concrete class satisfies structurally.

### `Domain` (`api/domain.py`) — the deployment seam

`main.py` picks a deployment by `DEPLOYMENT_TYPE`, looks it up in `api/deployments.py` `DOMAINS`, and builds
`make_beacon_router(domain)` + `make_lifespan(domain)`; the admin router mounts only when `ADMIN_KEY` is set. A new
deployment is a new `Domain` in `DOMAINS` (see `bigpicture/domain.py`). Besides the index name, beacon metadata,
filtering terms/scopes and `replace_concepts`, it carries `local_source` / `remote_source`
(`DocumentSource | None` — the `load` and `fetch` commands; `None` means that command has nothing to do), one
`beacon_service_factory` for the **query-less** service behind `/status`, `/health`, `/values`, `/suggestions` and
`ValueCountsUpdater`, and `query_endpoints: Sequence[BeaconQueryEndpoint]`, one per entity endpoint (`/datasets`,
`/images`) with its `path`, its own service factory, `result_sets_response_model` and — only under `FEATURE_AI` —
its AI persona and result model, mounted at `/ai<path>`; each endpoint needs its own service because each has a
different result shape. `make_lifespan` builds one term cache per ontology into `app.state.ontology_term_services`
and one `app.state.beacon_service`; routes must read **that** instance, since value counts are cached in a dict on
it and filled in the background, so a per-request one would start empty.

### Load path

**Postgres is primary.** `document` holds `id`, `payload` (JSONB, already in OpenSearch shape), `modified_at`,
`synced_at`; `LoadService` stores extracted documents, skipping any not newer than what is stored. `synced_at IS
NULL` is pending, and `SyncService` bulk-indexes those rows and stamps them, re-pushes included.

**`DocumentSource` (ABC, `services/fetch.py`) is the whole of what generic code knows about reading**:
`read(root, marker)`, an async generator yielding `SourceDocuments` — the marker reached once those documents are
stored, and the documents. `_read_documents` in `scripts/admin.py` is one loop over both sources; it reads no
configuration, opens no client, and does not know a unit is a dataset or a submission.

- `BigpictureLocalSource` walks the tree, one unit per dataset directory, and **dates every dataset before parsing
  any**: that orders them oldest first, skips one outside the period without reading its XML, and keeps one dataset
  in memory rather than the whole tree.
- `BigpictureRemoteSource` yields one unit per published submission, and **the archive is read by the very code
  that reads a directory** (an fsspec `ZipFileSystem` handed to `extract_dataset_documents`), so the two cannot
  drift. A zip entry's date is when the archive was built, so it stamps each document with the submission's
  publication date instead, and asks from `_FETCH_OVERLAP` before its marker.
- **The resume point is an opaque marker, not a date** (`database/load.py`, one upserted row), passed straight back
  to the source. Nothing moves it backwards, and it is written after the whole run, so a failure repeats.
- A fetch **signs a token per request** (`utils/token.py`, `private_key_jwt`, `exp` 60 s) and carries no `kid`, so
  a receiver rotating a key tries each; `GET /jwk` publishes the public key, so the *server* needs the signing key
  configured, not only `fetch`.

`ExtractedDocument` separates **`values`** (top level) from **`groups`** (one `OpenSearchGroup` per nested-group
item, which rejects a value belonging to another group). A field's indexed path is exactly `<nested_group>.<id>`,
and a dot in either part is rejected. **Scope** partitions documents rather than filtering them, so it is indexed
at the root under `SCOPE_FIELD`. **Extraction reaches no database** — what a load could not make sense of rides
out on `logs` and becomes `document_log` rows, replaced on every reload.

### Bigpicture extraction (`api/bigpicture/extract/`)

`extract_dataset_documents(root, fs, keys)` reads one dataset directory's six `METADATA/*.xml` files — `dataset`
(id, title, description), `image` (ids, slide mappings), `policy` (scope), `sample` (biological beings, cases,
specimens, blocks), `staining`, and `observation` (diagnoses and findings, the only optional one).
`dataset_files(fs, root)` resolves the same six paths alone (plain or `.c4gh`), so `local.py` can date a dataset
without reading it. `scope` is the part of the policy's `type_of_dataset` before the `/`. Every `CODE_ATTRIBUTE`
contributes the pair `(CODE, MEANING)`, the meaning being the fallback when the code is no concept id; a value
whose scheme is not the field's ontology is dropped with an error, and an **`ontologyOrValue`** field takes its
code in precedence over free text, so `<id>_other` is filled only when no code was read. Fields and filtering
terms are declared in `config/fields.yaml` (loaded by `api/fields.py`), and the index mapping is generated from
them; an `ontology`/`ontologyOrValue` field may declare an `ontologyRestriction` (`concept_ids` +
`include_descendants`), which is deployment config and excluded from the API.

### Routes (`api/beacon/routes.py`, built by `make_beacon_router(domain)`)

| Endpoint | Description |
|---|---|
| `GET /info` `/filtering_terms` `/filtering_scopes` | metadata, filter definitions |
| `POST /datasets` `/images` | Beacon V2 search: images aggregated into datasets / one per image |
| `POST /ai/datasets` `/ai/images` | natural-language search (gated by `FEATURE_AI`) |
| `GET /filtering_terms/{field_id}/values` `/suggestions` | values with counts; autocomplete |
| `GET /status` | documents indexed and pending, total and per scope, last sync time |
| `GET /health` | both stores answer; a `503` names the one that did not |
| `GET /jwk` | the public key this deployment signs fetches with |

`values`/`suggestions` accept `scope=<id>`; omitting it does not filter, and a scope the field does not declare is
a `400`. `AuthMiddleware` requires a session outside `PUBLIC_PATHS` — hence `/health` and `/info` anonymous,
`/status` a `401`. `/admin` is gated by `ADMIN_KEY`; `POST /admin/caches/reload` pushes a write to a running server.

### Query path

`register_query_route(endpoint)` registers `/datasets` and `/images` from one handler; they differ only in which
`BeaconQueryService` answers (by the endpoint's path) and which response model wraps the result. Ontology filters
resolve first through `prepare_ontology_filter` (expanding to descendants on `includeDescendantTerms`); the filter
`type` then picks a builder in `api/opensearch/clauses.py` — `text` → match, `controlledValue`/`keyword` → term(s),
`ontology`/`ontologyOrValue` → term, `iso8601Range` → range in days.

- **A filter only constrains the scopes its field is indexed for**, so `_get_query_clause` emits one `should`
  branch per scope when any filter is scope-specific: filtering on clinical-only `diagnosis` leaves non-clinical
  documents untouched rather than excluding them all.
- Filters over several fields combine with `build_or_clause`; **every filter on one nested group lands in a single
  nested query**, so it must hold for the very item that matched.
- **Value counts are document counts**: a bucket inside a `nested` aggregation counts items, so a `reverse_nested`
  sub-aggregation climbs back to documents. `ValueCountsUpdater` refills them when `max(synced_at)` moves, so a
  document bulk-indexed without a sync leaves the counts stale.

### Ontologies (`services/ontology/`)

`OntologyService` (ABC) abstracts one ontology. **Shape and membership are separate questions**: `is_well_formed`
answers from the value alone and never asks the ontology, `is_known` asks it. Beside them sit `get_preferred_terms`,
`is_retired`, `replacement_concept_id` and the template methods `prepare_ontology_filter` and
`is_within_restriction`; a provider implements only `_find_concept_ids`, `_find_descendant_ids` and
`_is_within_restriction`, and **every one is registered in `registrations.py`**, imported for the side effect.
`build_snomed_ecl` turns an `ontologyRestriction` into ECL, so a restriction and the expression for it never drift.

`resolve_concept_ids` tries the cheapest source first: **(1)** a value shaped like a concept id is taken as given
(an id absent from the ontology is absent from the index too); **(2)** a preferred term cached for the field,
covering every concept actually indexed, including ones outside the `ontologyRestriction`; **(3)** the provider's
hook. Unresolved values survive the prepared filter only for `ontologyOrValue`. **A load resolves through the same
cascade** (`ontology/values.py`), so a value indexed and a value searched for reach the same concept: the source's
code is kept when the ontology has it, and only otherwise is the **meaning** beside it resolved — one match a
`WARNING`, none an `ERROR`, and several an `ERROR` too unless the field's `ontologyRestriction` picks out exactly
one. A retired concept is then substituted, which buys reach rather than a name: retiring one strips its
relationships, so no subtree query reaches a document citing it.

**A load enforces the `ontologyRestriction`** as well as resolving through it: a concept outside the field's part
of the ontology is dropped with an `ERROR` rather than indexed. A restriction is answered by walking **up** from
the concept — SNOMED filters one ECL query by the concept id, the cached ontology walks parent ids in memory —
since a concept's ancestry is small whatever the restricted subtree costs to materialise; a retired concept passes,
having no relationships left to place it. A concept **already in the term cache skips both the membership and the
restriction check**, which is what keeps a reload off the network, so *tightening a restriction in `fields.yaml`
takes effect only once that cache is cleared*.

`SnomedService` wraps Snowstorm (`SNOWSTORM_URL`, branch `"MAIN"`), which always returns its best match — so a
value is accepted only if it **is** one of the concept's descriptions. `ontology/cache/` holds one whole small flat
ontology (SEND) in memory instead, re-fetching only on `refresh send`. `term_cache.py` maps `(ontology_id,
field_id, concept_id)` → preferred term, and **every ontology has one**: the part a field uses, behind `/values`.

### Caches, index, configuration

`UpdatedPoller` (`services/poller.py`) is the one reload loop every cache shares, each being filled from a store
another process writes: it reads `updated_at` **before** the refresh, so a write landing during it is seen by the
next poll, and records it only after the refresh **succeeds**. `specimen`/`staining`/`observation` are **nested**;
code fields are `keyword`; `age_at_extraction` is an `integer_range` of days. Text fields use the built-in
`english` analyzer, so the generated index needs no `settings`; changing it requires a recreate and reload.
`build_match_clause` sets `minimum_should_match` to `2<75%`, so a two-word query behaves like `and` — the `or`
default would match on one word, far too broad given that results are never ranked.

Settings (`conf.py`) are mostly **required** — no hardcoded host/db/password. Defaults: `POSTGRES_PORT=5432`,
`POSTGRES_POOL_{MIN_SIZE=2,MAX_SIZE=10,MAX_LIFETIME=3600,TIMEOUT=5}`, `OPENSEARCH_PORT=9200`, `DEPLOYMENT_ENV=dev`,
`{TERM,ONTOLOGY,VALUE_COUNT}_CACHE_REFRESH=300`, `FEATURE_AI=false`, `ADMIN_KEY=None`, `OIDC_SECURE_COOKIE=true`,
`JWT_ALGORITHM=HS256`. There is **one class per source**, since a `BaseSettings` validates every field it declares:
bundled, a `load <dir>` would demand submit API settings it never uses. **The server pools its Postgres connections
and nothing else does** (`database/repository.py`): with no pool open, `get_connection()` connects directly.

## Tests

`tests/` mirrors the package. `tests/unit/` is what `tox` runs and needs nothing external; `tests/integration/`
needs Postgres and OpenSearch (`tests/integration/.env` is a working config set); `tests/performance/` is Locust.
Integration `conftest.py` gives `bp_opensearch_docs` (override with inline documents) and `bp_opensearch_index` (a
`bp-image-index-test-<uuid>`, so runs are isolated). `@pytest.mark.requires_snowstorm` is skipped by
`SKIP_SNOWSTORM_TESTS=true`, set in CI, which cannot reach the internal-only Snowstorm, while
`@pytest.mark.requires_submit` is **probed**: skipped when `SD_SUBMIT_API_URL` answers nothing or `404`, so a
submitter that stopped authenticating fails rather than skipping.
