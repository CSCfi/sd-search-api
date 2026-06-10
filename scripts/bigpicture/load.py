"""Load Bigpicture data from XML files into the database."""

import argparse
import asyncio
import logging
import warnings

from dotenv import load_dotenv

from search_api.bigpicture.services.extract import BigPictureExtractService
from search_api.bigpicture.services.sync import BigPictureSyncService
from search_api.database.repository import get_cursor


async def main(
    directory: str,
    multi_dir: bool,
    sync: bool,
    c4gh_private_key_file: str | None,
    c4gh_passphrase: str | None,
) -> None:
    await BigPictureExtractService().extract_and_load_fields(
        root=directory,
        single_dir=not multi_dir,
        c4gh_private_key_file=c4gh_private_key_file,
        c4gh_passphrase=c4gh_passphrase,
    )

    if sync:
        sync_service = BigPictureSyncService()
        try:
            async with get_cursor() as cur:
                await sync_service.sync_fields(cur)
        finally:
            await sync_service.search.close()


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
    parser.add_argument(
        "--c4gh-key-file",
        default=None,
        metavar="FILE",
        help="Path to a Crypt4GH private key file (.sec) for decrypting .c4gh files.",
    )
    parser.add_argument(
        "--c4gh-passphrase",
        default=None,
        metavar="PASSPHRASE",
        help="Passphrase for the Crypt4GH private key (omit for unprotected keys).",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    warnings.filterwarnings("ignore", category=UserWarning, module="opensearchpy")

    if args.env_file:
        load_dotenv(args.env_file)

    asyncio.run(
        main(
            args.directory,
            args.multi_dir,
            args.sync,
            args.c4gh_key_file,
            args.c4gh_passphrase,
        )
    )
