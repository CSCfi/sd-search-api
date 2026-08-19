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

The first positional argument selects the deployment to act on. Every command under 
it acts on that deployment's own Postgres database and OpenSearch index.

The exception is `snomed`, which sits beside the deployments rather. The Snowstorm 
is a single server shared by all of the deployments.

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

Then run the CLI in another terminal:

```bash
uv run python scripts/admin.py ...
```

As an alternative to exporting the variables, `--env-file <path>` loads them from a file. It goes
before the command group:

```bash
uv run python scripts/admin.py --env-file <path> ...
```

`tests/integration/.env` is a working set for local integration tests.

### Bigpicture

#### Load datasets

```bash
uv run python scripts/admin.py Bigpicture load /path/to/dataset/
```
Options:
- `--multi-dir` — the directory holds several dataset subdirectories instead of one dataset.
- `--dry-run` — parse and validate the sources, writing nothing.
- `--sync` — sync to OpenSearch after loading.
- `--c4gh-key-file FILE`, `--c4gh-passphrase PASSPHRASE` — decrypt `.c4gh` XML files while reading them.

#### Sync documents to OpenSearch

Send the documents pending sync to OpenSearch.

```bash
uv run python scripts/admin.py Bigpicture sync
```

#### Clear all data

Delete every document from the database and the OpenSearch index, and 
every preferred term cache.

```bash
uv run python scripts/admin.py Bigpicture clear
```

The deployment name has to be typed to confirm. Refused when `DEPLOYMENT_ENV=prod`.

#### Recreate the database schema and the OpenSearch index

Drop and recreate both stores, discarding all data.

```bash
uv run python scripts/admin.py Bigpicture recreate
```

The deployment name has to be typed to confirm. Refused when `DEPLOYMENT_ENV=prod`.

#### Generate the OpenSearch index mapping

Write `search_api/api/bigpicture/index/bp-image-index.json` from the field definitions,
and commit the file.

```bash
uv run python scripts/admin.py Bigpicture index generate
```

#### Create the OpenSearch index

Create the index from the generated mapping. Required once per environment 
before the first sync, and fails if the index already exists.

```bash
uv run python scripts/admin.py Bigpicture index create
```

#### Recreate the OpenSearch index

Drop the index and create it from the generated mapping, leaving the database untouched. 
Marks every document as pending sync, so follow it with `sync`.

```bash
uv run python scripts/admin.py Bigpicture index generate 
uv run python scripts/admin.py Bigpicture index recreate
uv run python scripts/admin.py Bigpicture sync
```

The deployment name has to be typed to confirm. Refused when `DEPLOYMENT_ENV=prod`.

#### Refresh an ontology cache

Refresh the preferred terms this deployment caches for an ontology, updating the ontology from its
source first when the database caches it whole (e.g. SEND). SNOMED CT is served by
Snowstorm, so only its terms are refreshed.

```bash
uv run python scripts/admin.py Bigpicture refresh snomed
uv run python scripts/admin.py Bigpicture refresh send
```

### Snowstorm

One Snowstorm serves all deployments.

#### Import a SNOMED CT release

Import a release into Snowstorm, automating the procedure under
[Import SNOMED release](#import-snomed-release). Follow this up
with each deployment's `refresh snomed`.

```bash
uv run python scripts/admin.py snomed import --release-file /path/to/SnomedCT_InternationalRF2_PRODUCTION_<date>.zip
uv run python scripts/admin.py Bigpicture refresh snomed
```

## LLM search

The experimental Bigpicture LLM search endpoint uses a small local [Ollama](https://ollama.com) model. Install and
start it before running the API:

```bash
brew install ollama
ollama pull qwen2.5:14b
ollama serve
```

## Performance tests

See [tests/performance/README.md](tests/performance/README.md).
