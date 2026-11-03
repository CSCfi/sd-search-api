from psycopg import AsyncCursor

from search_api.database.document import DOCUMENT_TABLE, get_document
from tests.utils.bigpicture import DATASET_IMAGES


async def assert_dataset_images_stored(cur: AsyncCursor) -> None:
    """Assert every dataset image is stored with its dataset and scope."""
    for image_id, image in DATASET_IMAGES.items():
        document = await get_document(cur, image_id)
        assert document is not None, f"{image_id!r} was not loaded"
        assert document["image_id"] == image_id
        assert document["dataset_id"] == image.dataset_id
        assert document["scope"] == image.scope


async def delete_dataset_images(cur: AsyncCursor) -> None:
    """Delete stored dataset images from the database."""
    for image_id in DATASET_IMAGES:
        await cur.execute(f"DELETE FROM {DOCUMENT_TABLE} WHERE id = %s", (image_id,))
