from abc import ABC, abstractmethod
from typing import Any, override

from psycopg import AsyncConnection

from opensearchpy import AsyncOpenSearch


class BigpictureBeaconService(ABC):
    """
    Abstract Bigpicture Beacon search.
    """

    @abstractmethod
    async def query(
        self,
        filters: list[dict[str, Any]],
        skip: int,
        limit: int,
        include_image_ids: bool,
    ) -> dict[str, Any]:
        """
        Execute a Beacon query.
        """
        pass


def get_mock_results_sets(include_image_ids: bool) -> list[Any]:
    return [
        {
            "id": "testDataset",
            "resultsCount": 1,  # total matching image count
            "results": [
                {
                    "datasetId": "testDataset",
                    "datasetTitle": "testTitle",
                    "datasetDescription": "testDescription",
                    "totalImageCount": 1,
                    "matchingImageCount": 1,
                    "imageIds": ["img1"] if include_image_ids else [],
                }
            ],
        }
    ]


class MockBigpictureBeaconService(BigpictureBeaconService):
    """
    Mock Bigpicture Beacon search.
    """

    @override
    async def query(
        self,
        filters: list[dict[str, Any]],
        skip: int,
        limit: int,
        include_image_ids: bool,
    ) -> dict[str, Any]:
        return {"result_sets": get_mock_results_sets(include_image_ids)}


class PostgresBigpictureBeaconService(BigpictureBeaconService):
    """
    Postgres Bigpicture Beacon search.
    """

    def __init__(self, conn: AsyncConnection):
        self.conn = conn

    async def query(
        self,
        filters: list[dict[str, Any]],
        skip: int,
        limit: int,
        include_image_ids: bool,
    ) -> dict[str, Any]:
        where_clauses = []
        params = []

        # TEXT fields with free text search (tsvector)
        free_text_field_ids = {
            "dataset_title": "bp_image.dataset_title_tsv",
            "dataset_description": "bp_image.dataset_description_tsv",
        }

        # TEXT[] fields
        text_array_field_ids = {
            "species": "bp_image.species",
            "anatomical_site": "bp_image.anatomical_site",
            "sex": "bp_image.sex",
            "fixation_type": "bp_image.fixation_type",
            "block_preparation": "bp_image.block_preparation",
            "specimen_type": "bp_image.specimen_type",
        }

        # JSONB stains field
        stains = {}

        # INT4RANGE age_at_extraction field
        is_age_of_extraction = False

        # Process filters.
        for f in filters:
            filter_id = f["id"]
            filter_value = f["value"]

            # TEXT fields with free text search (tsvector)
            if filter_id in free_text_field_ids:
                where_clauses.append(
                    f"{free_text_field_ids[filter_id]} @@ websearch_to_tsquery('english', %s)"
                )
                params.append(filter_value)

            # TEXT[] fields
            elif filter_id in text_array_field_ids:
                where_clauses.append(f"{text_array_field_ids[filter_id]} @> %s")
                params.append([filter_value])

            # JSONB stains field
            elif filter_id.startswith("staining."):
                field = filter_id.split(".", 1)[1]
                stains[field] = filter_value

            # INT4RANGE age_at_extraction field
            elif filter_id == "age_at_extraction":
                is_age_of_extraction = True

                min_filter_value = filter_value.get("min")
                max_filter_value = filter_value.get("max")

                where_clauses.append(
                    "bp_image_extraction.age_at_extraction && int4range(%s, %s)"
                )
                params.extend([min_filter_value, max_filter_value])

        # JSONB stains field
        if stains:
            where_clauses.append("bp_image.stains @> %s::jsonb")
            params.append([stains])

        # Construct where clause
        where_sql = ""
        if where_clauses:
            where_sql = "WHERE " + " AND ".join(where_clauses)

        # Join bp_image_extraction
        join_sql = ""
        if is_age_of_extraction:
            join_sql = "LEFT JOIN bp_image_extraction ON bp_image.image_id = bp_image_extraction.image_id"

        # Get image ids
        if include_image_ids:
            image_ids_sql = (
                f"ARRAY_AGG(bp_image.image_id ORDER BY bp_image.image_id)[:{limit}]"
            )
        else:
            image_ids_sql = "ARRAY[]::text[]"

        # Construct full query
        sql = f"""
                SELECT
                    bp_image.dataset_id,
                    MAX(bp_image.dataset_title) AS dataset_title,
                    MAX(bp_image.dataset_description) AS dataset_description,
                    MAX(bp_image.dataset_image_cnt) AS total_image_count,
                    COUNT(*) AS matching_image_count,
                    {image_ids_sql} AS image_ids
                FROM bp_image
                {join_sql}
                {where_sql}
                GROUP BY bp_image.dataset_id
                ORDER BY matching_image_count DESC
                OFFSET %s
            LIMIT %s
        """

        params.extend([skip, limit])

        async with self.conn.cursor() as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()

        result_sets = []

        for row in rows:
            dataset_id = row[0]
            dataset_title = row[1]
            dataset_description = row[2]
            total_image_count = row[3]
            matching_image_count = row[4]
            image_ids = row[5] or []

            result_sets.append(
                {
                    "id": dataset_id,
                    "resultsCount": matching_image_count,
                    "results": [
                        {
                            "datasetId": dataset_id,
                            "datasetTitle": dataset_title,
                            "datasetDescription": dataset_description,
                            "totalImageCount": total_image_count,
                            "matchingImageCount": matching_image_count,
                            "imageIds": image_ids if include_image_ids else [],
                        }
                    ],
                }
            )

        return {"result_sets": result_sets}


class OpenSearchBigpictureBeaconService(BigpictureBeaconService):
    """
    OpenSearch Bigpicture Beacon search.
    """

    def __init__(self, client: AsyncOpenSearch, index_name: str):
        self.client = client
        self.index_name = index_name

    async def query(
        self,
        filters: list[dict[str, Any]],
        skip: int,
        limit: int,
        include_image_ids: bool,
    ) -> dict[str, Any]:

        must_clauses = []

        # Keyword fields
        keyword_fields = {
            "species": "species",
            "anatomical_site": "anatomical_site",
            "sex": "sex",
            "fixation_type": "fixation_type",
            "block_preparation": "block_preparation",
            "specimen_type": "specimen_type",
        }

        # Full-text search fields
        text_fields = {
            "dataset_title": "dataset_title",
            "dataset_description": "dataset_description",
        }

        # Stain search fields
        stain_fields = {
            "staining.method": "stains.staining_method",
            "staining.target": "stains.staining_target",
            "staining.procedure": "stains.staining_procedure",
            "staining.compound": "stains.staining_compound",
        }
        stain_filters = {}

        # Build query
        for f in filters:
            field_id = f["id"]
            field_value = f["value"]

            # Keyword fields
            if field_id in keyword_fields:
                must_clauses.append({"term": {keyword_fields[field_id]: field_value}})

            # Full-text search fields
            elif field_id in text_fields:
                must_clauses.append({"match": {text_fields[field_id]: field_value}})

            # Nested stains

            elif field_id in stain_fields:
                stain_filters[stain_fields[field_id]] = field_value

            # Age at extraction range
            elif field_id == "age_at_extraction":
                range_query = {}

                if "min" in field_value:
                    range_query["gte"] = field_value["min"]
                if "max" in field_value:
                    range_query["lte"] = field_value["max"]

                must_clauses.append({"range": {"age_at_extraction": range_query}})

        # Nested stains
        if stain_filters:
            nested_must = [
                {"term": {field: value}} for field, value in stain_filters.items()
            ]

            must_clauses.append(
                {"nested": {"path": "stains", "query": {"bool": {"must": nested_must}}}}
            )

        _query: dict[str, Any] = {
            "size": 0,  # Return only dataset aggregation and not individual image documents.
            "query": {
                "bool": {"must": must_clauses if must_clauses else [{"match_all": {}}]}
            },
            "aggs": {
                "datasets": {
                    "terms": {
                        "field": "dataset_id",
                        "size": skip + limit,
                        "order": {"_count": "desc"},
                    },
                    "aggs": {
                        # Return one representative document per dataset. Number
                        # of matched images is returned in 'doc_count' field
                        # for each 'key' (dataset id) field.
                        "dataset_metadata": {
                            "top_hits": {
                                "size": 1,
                                "_source": [
                                    "dataset_title",
                                    "dataset_description",
                                    "dataset_image_cnt",
                                ],
                            }
                        },
                        # Bucket contains all documents (images) that have the same dataset_id.
                        "bucket_pagination": {
                            "bucket_sort": {"from": skip, "size": limit}
                        },
                    },
                }
            },
        }

        # ---- optional sample image_ids ----
        if include_image_ids:
            _query["aggs"]["datasets"]["aggs"]["images"] = {
                "top_hits": {"size": limit, "_source": ["image_id"]}
            }

        # Execute
        resp = await self.client.search(index=self.index_name, body=_query)

        buckets = resp["aggregations"]["datasets"]["buckets"]

        result_sets = []

        for bucket in buckets:
            dataset_id = bucket["key"]
            matching_count = bucket["doc_count"]

            hits = bucket["dataset_metadata"]["hits"]["hits"]
            hit_source = hits[0]["_source"] if hits else {}

            dataset_title = hit_source.get("dataset_title")
            dataset_description = hit_source.get("dataset_description")
            total_image_count = hit_source.get("dataset_image_cnt")

            image_ids = []

            if include_image_ids:
                hits = bucket["images"]["hits"]["hits"]
                image_ids = [h["_source"]["image_id"] for h in hits]

            result_sets.append(
                {
                    "id": dataset_id,
                    "resultsCount": matching_count,
                    "results": [
                        {
                            "datasetId": dataset_id,
                            "datasetTitle": dataset_title,
                            "datasetDescription": dataset_description,
                            "totalImageCount": total_image_count,
                            "matchingImageCount": matching_count,
                            "imageIds": image_ids,
                        }
                    ],
                }
            )

        return {"result_sets": result_sets}
