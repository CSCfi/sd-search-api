# SD Search API

## Description

The SD Search API enables search across different datasets.

Supported configurations:

- Bigpicture image search

## Dependencies

- PostgreSQL: database for search metadata
- OpenSearch: search indexes build from the search metadata
- Snowstorm: SNOMED CT ontology server

### OpenSearch

OpenSearch indexes:

- Bigpicture: `bp-image-index.json`

## Development

### Setup

Install [uv](https://docs.astral.sh/uv/), then create the virtualenv and install all dependencies:

```bash
uv sync --dev
```

Activate the pre-commit hook to run `tox` before every commit:

```bash
uv run pre-commit install
```

### Formatting and linting

```bash
tox -e ruff
tox -e mypy
```

### Unit tests

```bash
tox -e pytest
```

### Integration tests

Integration tests require Postgres and OpenSearch to be running. Start
them with Docker Compose:

```bash
docker compose --env-file tests/integration/.env --profile dev up --build -d
```

Then run:

```bash
uv run pytest tests/integration/
```

Environmental variables are defined in `tests/integration/.env`.

### Running the API and UI locally

The UI component of SD Search is available at: https://github.com/CSCfi/sd-search-ui

To run them both locally at the same time, first start the API stack, redirecting the OIDC login flow back to the UI
instead of the API itself:

```bash
OIDC_REDIRECT_URL=http://localhost:8081/search BASE_URL=http://localhost:8081 docker compose --env-file tests/integration/.env --profile dev up --build -d
```

Then, from the `sd-search-ui` repository, start the UI joined to the same Compose project so it can reach the API in the
same Docker network:

```bash
COMPOSE_PROJECT_NAME=sd-search-api docker compose up --build -d
```

The UI service will then be available at http://localhost:8081

## Deployment

Build the Docker image for deployment and push it to the image container registry used by the OpenShift's ImageStream
triggering rollout automatically.

```bash
docker build --platform=linux/amd64 -f dockerfiles/Dockerfile -t <image-registry-url>/sd-search-api:latest .

docker push <image-registry-url>/sd-search-api:latest
```

## External dependencies

### Snowstorm

[Snowstorm](https://github.com/IHTSDO/snowstorm) is a SNOMED CT terminology server used by the SD Search API
to resolve SNOMED CT terms to concepts.

- A Snowstorm instance is available at `https://snowstorm.rahtiapp.fi`.
- A SNOMED browser instance is available at: `https://snomed-browser.rahtiapp.fi/`.

#### Data import

This is only needed when importing a new SNOMED CT release into the shared instance. The full procedure is described
in https://github.com/IHTSDO/snowstorm/blob/master/docs/loading-snomed.md.

First check that the Snowstorm service is healthy:

```
curl https://snowstorm.rahtiapp.fi/actuator/health
```

Expected output:

```
{"status":"UP","groups":["liveness","readiness"]}%       
```

#### Create import job

```
curl -i --location 'https://snowstorm.rahtiapp.fi/imports' \
  --header 'Content-Type: application/json' \
  --data '{"type":"SNAPSHOT","branchPath":"MAIN","createCodeSystemVersion":true}'
```

Example output:

```
HTTP/1.1 201 
location: https://snowstorm.rahtiapp.fi/imports/<ID>
```

Get the import ID (e.g. f0801e81-3740-48bd-bc3e-848c7aa7468e) from the response location header
and define the IMPORT_ID environmental variable:

```
export IMPORT_ID=<ID>
```

#### Import SNOMED release

Upload SNOMED release file (e.g. SnomedCT_InternationalRF2_PRODUCTION_20260601T120000Z.zip):

```
curl --location -X POST "https://snowstorm.rahtiapp.fi/imports/${IMPORT_ID}/archive" \
  -F "file=@<SNOMED release file>"
```

The upload and import can take several hours. Poll the import status until `status` is `COMPLETED`
or until the import job is no longer available:

```
curl --location "https://snowstorm.rahtiapp.fi/imports/${IMPORT_ID}"
```

Example output while running:

```
{
  "status" : "RUNNING",
  "type" : "SNAPSHOT",
  "branchPath" : "MAIN",
  "internalRelease" : false,
  "moduleIds" : [ ],
  "createCodeSystemVersion" : true
}
```

You can monitor the import progress also from the logs:

```
oc logs -f deployment/snowstorm
```

Once finished, verify that the import has been completed.

Check the imported versions:

```
curl -s https://snowstorm.rahtiapp.fi/codesystems/SNOMEDCT/versions | jq '.items[] | {version, branchPath}'
```

Example output:

```
{
  "version": "2026-06-01",
  "branchPath": "MAIN/2026-06-01"
}
```

Check the MAIN branch:

```
curl -s https://snowstorm.rahtiapp.fi/branches/MAIN                                     
```

Example output:

```
{
  "path" : "MAIN",
  "state" : "UP_TO_DATE",
  "containsContent" : true,
  "locked" : false,
  "creation" : "2026-06-11T05:12:34.688Z",
  "base" : "2026-06-11T05:12:34.688Z",
  "head" : "2026-06-11T05:52:38.457Z",
  "creationTimestamp" : 1781154754688,
  "baseTimestamp" : 1781154754688,
  "headTimestamp" : 1781157158457,
  ...
}
```

Get number of concepts:

```
curl -s "https://snowstorm.rahtiapp.fi/MAIN/concepts?limit=1&active=true" | jq '{total}'
```

Example output:

```
{
  "total": 532824
}
```

Get a concept:

```
curl -s "https://snowstorm.rahtiapp.fi/MAIN/concepts/337915000" | jq '{conceptId, active, fsn: .fsn.term}'
```

Example output:

```
{
  "conceptId": "337915000",
  "active": true,
  "fsn": "Homo sapiens (organism)"
}
```

## Using admin.py

The first positional argument selects a command group: a deployment or an ontology
(`snomed`, `send`).

### Prepare environment

The commands connect directly to Postgres and OpenSearch, so the environment must define
`POSTGRES_HOST`,`POSTGRES_PORT`,`POSTGRES_DB`,`POSTGRES_USER`,`POSTGRES_PASSWORD` and
`OPENSEARCH_HOST`,`OPENSEARCH_PORT`,`OPENSEARCH_USER`,and `OPENSEARCH_PASSWORD`. Loading 
and the ontology commands also resolve preferred terms, that require `SNOWSTORM_URL`.

Running against a deployed environment means logging in to OpenShift, taking the credentials from
Vault, and port-forwarding OpenSearch to localhost.

1. Log in to the [Rahti web console](https://rahti.csc.fi/).
2. Click your username in the top right corner.
3. Select "Copy Login Command" from the dropdown menu.
4. Authenticate again when asked.
5. Copy the resulting `oc login ... --token=...` command and paste it into your terminal.

Activate the project:

```bash
oc project sd-search
```

Take the values from
[Vault](https://vault.sdd.csc.fi:8200/ui/vault/secrets-engines/secret/kv/sd-search/details) and
export them:

```bash
export POSTGRES_HOST=
export POSTGRES_PORT=
export POSTGRES_DB=
export POSTGRES_USER=
export POSTGRES_PASSWORD=
export OPENSEARCH_PORT=
export OPENSEARCH_USER=
export OPENSEARCH_PASSWORD=
export SNOWSTORM_URL="https://snowstorm.rahtiapp.fi"
```

OpenSearch is reached through a port forward rather than directly, so its host is localhost:

```bash
export OPENSEARCH_HOST=localhost
```

Start the port forward. It runs in the foreground, so leave it in its own terminal, and make sure
`OPENSEARCH_PORT` is the local side of the forward:

```bash
oc port-forward svc/opensearch 9200:9200
```

Then run the CLI in another terminal, e.g.:

```bash
uv run python scripts/admin.py Bigpicture load /path/to/datasets/ --multi-dir --load --sync
```

As an alternative to exporting the variables, `--env-file <path>` loads them from a file. It goes
before the command group:

```bash
uv run python scripts/admin.py --env-file <path> Bigpicture load /path/to/datasets/ --load
```

`tests/integration/.env` is a working set for local integration tests.

### Bigpicture

#### Load datasets

Load a single dataset directory (default):

```bash
uv run python scripts/admin.py Bigpicture load /path/to/dataset/ --load
```

Load from a parent directory containing multiple dataset subdirectories:

```bash
uv run python scripts/admin.py Bigpicture load /path/to/datasets/ --multi-dir --load
```

Omit `--load` to parse XMLs without loading them to the database.

To also sync to OpenSearch immediately after loading, add `--sync`:

```bash
uv run python scripts/admin.py Bigpicture load /path/to/datasets/ --multi-dir --load --sync
```

`.c4gh`-encrypted XML files are decrypted while they are read, given the key to decrypt them with:

```bash
uv run python scripts/admin.py Bigpicture load /path/to/datasets/ --load \
    --c4gh-key-file /path/to/key.sec --c4gh-passphrase <passphrase>
```

#### Clear all data

Delete every document from both the database and the OpenSearch index, together with the preferred
terms cached for them, e.g. to reload a dataset from scratch:

```bash
uv run python scripts/admin.py Bigpicture clear
```

The deployment name has to be typed to confirm. Refused when `DEPLOYMENT_ENV=prod`. Only the terms
cached for this deployment's own fields are deleted. The terms are 
cached again as the documents are loaded:

```bash
uv run python scripts/admin.py Bigpicture load /path/to/datasets/ --multi-dir --load --sync
```

#### Recreate the database schema and the OpenSearch index

Drop and rebuild both stores, discarding all documents *and* the cached ontology terms. The 
index is recreated from the generated mapping, so run `generate-index` first if 
the field definitions changed:

```bash
uv run python scripts/admin.py Bigpicture recreate
```

The deployment name has to be typed to confirm. Refused when `DEPLOYMENT_ENV=prod`. Everything then
has to be reloaded, and the ontology caches refreshed:

```bash
uv run python scripts/admin.py send refresh
uv run python scripts/admin.py Bigpicture load /path/to/datasets/ --multi-dir --load --sync
```

#### Generate the OpenSearch index

The OpenSearch index mapping (`search_api/api/bigpicture/index/bp-image-index.json`) is
generated from the filtered and non-filtered field definitions, so that field names
and types stay in sync with them.
After changing them, regenerate and commit the file:

```bash
uv run python scripts/admin.py Bigpicture generate-index
```

A unit test fails if this file differs from a freshly generated one.

#### Create the OpenSearch index in a new environment

`generate-index` only writes the mapping to a local file — it does not create the index in
OpenSearch. **A new OpenSearch instance needs the index created from that mapping before the
first `--sync`.** If documents are synced into an index that doesn't exist yet, OpenSearch
silently auto-creates it with a dynamic mapping (e.g. `keyword` fields become `text`, and
`nested` fields become plain objects), which breaks aggregations and nested queries in ways
that only surface later, disconnected from the actual cause.

Create the index explicitly:

```bash
uv run python scripts/admin.py Bigpicture create-index
```

This fails loudly if the index already exists, rather than silently leaving a stale mapping in
place. If an index was already auto-created with the wrong mapping, OpenSearch cannot change an
existing field's type in place, so it must be deleted and recreated, and previously-synced
documents must be resynced:

```bash
curl -X DELETE https://<opensearch-host>:9200/bp-image-index -u <user>:<password>
uv run python scripts/admin.py Bigpicture create-index
# Reset sync state so the next --sync repopulates the recreated index:
#   UPDATE document SET synced_at = NULL;
uv run python scripts/admin.py Bigpicture load <dir> --load --sync
```

### Ontologies

These commands are not tied to a deployment: every deployment resolves its terms against the same
caches.

#### Refresh SNOMED CT

After a new SNOMED CT release, import it into Snowstorm and update the preferred terms cached for it
in the database. `--release-file` is required and takes the release archive:

```bash
uv run python scripts/admin.py snomed refresh --release-file /path/to/SnomedCT_InternationalRF2_PRODUCTION_<date>.zip
```

The import is the procedure described under [Import SNOMED release](#import-snomed-release), done for
you: the job is created, the archive uploaded, and the command polls until Snowstorm reports it
completed.

#### Refresh SEND

SEND is small and simple enough to cache whole, so the concept table itself lives in the database
rather than in a terminology server. This fetches the current release from NCI EVS, and updates the
stored table and the preferred terms cached for it:

```bash
uv run python scripts/admin.py send refresh
```

A release no newer than the stored one is skipped. A running server picks the new table up on 
its next poll, without a restart.

## LLM search

The experimental Bigpicture LLM search endpoint uses a small local [Ollama](https://ollama.com) model. Install and
start it before running the API:

```bash
brew install ollama
ollama pull qwen2.5:14b
ollama serve
```

The `/ai/query` endpoint accepts a query for the LLM search. The LLM translates
the query text into Beacon V2 filters and returns structured results.

Example:

```bash
curl -X POST "http://localhost:8000/ai/query" \
  -H "Content-Type: application/json" \
  -d '{"query": "images for human females"}'
```

## Performance tests

See [tests/performance/README.md](tests/performance/README.md).
