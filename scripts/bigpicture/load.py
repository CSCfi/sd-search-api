"""Load Bigpicture data from XML files into the database."""

import argparse
import asyncio
import logging

from dotenv import load_dotenv

from search_api.bigpicture.services.extract import BigPictureExtractService
from search_api.bigpicture.services.sync import BigPictureSyncService
from search_api.database.repository import get_cursor


async def main(directory: str, multi_dir: bool, sync: bool) -> None:
    await BigPictureExtractService().extract_and_load_fields(
        root=directory,
        single_dir=not multi_dir,
    )

    if sync:
        async with get_cursor() as cur:
            await BigPictureSyncService().sync_fields(cur)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Load Bigpicture data from XML files into the database."
    )
    parser.add_argument(
        "directory",
        help="Directory to load from.",
    )
    parser.add_argument(
        "--multi-dir",
        action="store_true",
        default=False,
        help=(
            "Treat directory as a parent directory containing multiple dataset "
            "subdirectories, instead of a single dataset directory."
        ),
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        default=False,
        help="Sync loaded data to OpenSearch after loading.",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        metavar="FILE",
        help="Path to a .env file to load environment variables from.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if args.env_file:
        load_dotenv(args.env_file)

    asyncio.run(main(args.directory, args.multi_dir, args.sync))
