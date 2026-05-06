from abc import ABC, abstractmethod
from typing import Any, override


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
