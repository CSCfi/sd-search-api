#!/bin/sh
set -e

# Start OpenSearch Dashboards in the background using the original entrypoint.
./opensearch-dashboards-docker-entrypoint.sh opensearch-dashboards &
DASHBOARDS_PID=$!

# Wait for Dashboards to be ready.
echo "Waiting for OpenSearch Dashboards to be ready..."
until curl -sf -u "admin:$OPENSEARCH_PASSWORD" http://localhost:5601/api/status > /dev/null 2>&1; do
  sleep 5
done
echo "OpenSearch Dashboards is ready."

# Create the bp-image-index pattern in the Global tenant (idempotent — ignores 409 if it already exists).
curl -s -X POST "http://localhost:5601/api/saved_objects/index-pattern" \
  -H "Content-Type: application/json" \
  -H "osd-xsrf: true" \
  -H "securitytenant: global" \
  -u "admin:$OPENSEARCH_PASSWORD" \
  -d '{"attributes":{"title":"bp-image-index","timeFieldName":""}}' \
  && echo "Index pattern 'bp-image-index' created." || echo "Index pattern 'bp-image-index' already exists."

# Hand control back to the Dashboards process.
wait $DASHBOARDS_PID
