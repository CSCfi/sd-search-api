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
docker compose --profile dev up --build
```

Then run:

```bash
.venv/bin/pytest tests/integration/
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

The upload and import can take 1-2 hours. Poll the import status until `status` is `COMPLETED`
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

Verify that the SNOMED CT ontology is loaded:

```bash
curl -s "https://snowstorm.rahtiapp.fi/codesystems" | jq '.items[].shortName'
```

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
