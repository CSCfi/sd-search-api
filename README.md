# SD Search API

## Description

The SD Search API enables search across different datasets. At first,
it will support Bigpicture image search.

## Development

### Postgres

Launch a Postgres container by running:

`docker compose --profile dev up --build`

## OpenSearch

Follow these instructions to launch an OpenSearch container:
https://oofnivek.medium.com/macos-running-opensearch-f0ccf3d55af7

Pull containers and create s network:

```
docker pull opensearchproject/opensearch:3.1.0
docker pull opensearchproject/opensearch-dashboards:3.1.0
docker network create my-opensearch-network
```

Start the OpenSearch container and check that it is running:

```
docker run -d \
  -p 9200:9200 -p 9600:9600 \
  -e "discovery.type=single-node" \
  -e "OPENSEARCH_JAVA_OPTS=-Xms512m -Xmx512m" \
  -e "plugins.security.disabled=true" \
  -e "DISABLE_INSTALL_DEMO_CONFIG=true" \
  --name my-opensearch-node \
  --network my-opensearch-network \
  opensearchproject/opensearch:3.1.0

curl -X GET "http://localhost:9200/"
```

Start the OpenSearch container dashboard and check that it is running:

```
docker run -d \
  -p 5601:5601 \
  -e "OPENSEARCH_HOSTS=[\"http://my-opensearch-node:9200\"]" \
  -e "DISABLE_SECURITY_DASHBOARDS_PLUGIN=true" \
  --name my-opensearch-dashboards \
  --network my-opensearch-network \
  opensearchproject/opensearch-dashboards:3.1.0

http://localhost:5601
```

Create bp-image-index index:

```
curl -X PUT "http://localhost:9200/bp-image-index" \
-H "Content-Type: application/json" \
-d '*1'
```

*1 Contents of the opensearch/bigpicture/bp-image-index.json file.

See the bp-image-index settings and mappings:

```
curl -X GET "http://localhost:9200/bp-image-index/_settings?pretty"
curl -X GET "http://localhost:9200/bp-image-index/_mapping?pretty"
```


