# SD Search API

## Description

The SD Search API enables search across different datasets.

Supported configurations:

- Bigpicture image search

## Development

### Bigpicture

#### Postgres and OpenSearch

Launch Postgres and OpenSearch containers by running:

`docker compose --profile dev up --build`

The Bigpicture index is defined in bp-image-index.json. It is created automatically
by generate_data.py when the containers are created.

The OpenSearch dashboard for the bp-image-index is available at:

http://localhost:5601/

See the bp-image-index settings and mappings:

```
curl -X GET "http://localhost:9200/bp-image-index/_settings?pretty"
curl -X GET "http://localhost:9200/bp-image-index/_mapping?pretty"
```

Create bp-image-index index:

```
curl -X PUT "http://localhost:9200/bp-image-index" \
  -H "Content-Type: application/json" \
  -d @search_api/opensearch/bigpicture/bp-image-index.json
```

Delete bp-image-index index:

```
curl -X DELETE "http://localhost:9200/bp-image-index"
```

#### Ollama

The experimental LLM search endpoint uses a small local [Ollama](https://ollama.com) model. Install and
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
