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
docker compose --env-file tests/integration/.env --profile dev up --build
```

Then run:

```bash
uv run pytest tests/integration/
```

Environmental variables are defined in `tests/integration/.env`.

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

## Data loading

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

Omit `--load` parse XMLs without loading them to the database.

To also sync to OpenSearch immediately after loading, add `--sync`:

```bash
uv run python scripts/admin.py Bigpicture load /path/to/datasets/ --multi-dir --load --sync
```

#### Refresh SNOMED CT preferred terms

After a new SNOMED CT release, update the stored preferred terms to match the new release:

```bash
uv run python scripts/admin.py Bigpicture snomed refresh
```

#### Generate the OpenSearch index

The OpenSearch index mapping (`search_api/opensearch/bigpicture/bp-image-index.json`) is
is generated from the filtered and non-filtered field definitions, so that field names
and types stay in sync with them.
After changing them, regenerate and commit the file:

```bash
uv run python scripts/admin.py Bigpicture generate-index
```

An unit test fails if this file is different from a freshy generated one.

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
