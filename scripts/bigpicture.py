"""Bigpicture search admin CLI."""

import argparse
import asyncio
import logging
import warnings

from dotenv import load_dotenv

from search_api.api.bigpicture.models import BP_SNOMED_TABLE
from search_api.bigpicture.services.extract import BigPictureExtractService
from search_api.bigpicture.services.load import BigPictureLoadService
from search_api.bigpicture.services.sync import BigPictureSyncService
from search_api.database.repository import get_cursor
from search_api.services.snomed import SnomedService
from search_api.services.snomed_term import PostgresSnomedTermCacheService


def _setup(env_file: str | None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    warnings.filterwarnings("ignore", category=UserWarning, module="opensearchpy")
    if env_file:
        load_dotenv(env_file)


async def _load(args: argparse.Namespace) -> None:
    fields_iter = BigPictureExtractService.extract_fields(
        root=args.directory,
        single_dir=not args.multi_dir,
        c4gh_private_key_file=args.c4gh_key_file,
        c4gh_passphrase=args.c4gh_passphrase,
    )

    if not args.load:
        logging.info("Extracting from %s without writing to the database.", args.directory)
        count = 0
        for fields in fields_iter:
            logging.info("Would load image %s (dataset %s).", fields.image_id, fields.dataset_id)
            count += 1
        logging.info("%d image(s) extracted without loading them.", count)
        return

    snomed_term_service = PostgresSnomedTermCacheService(BP_SNOMED_TABLE)
    snomed_service = SnomedService()

    logging.info("Loading fields from %s.", args.directory)
    load_service = BigPictureLoadService(
        snomed_term_service=snomed_term_service,
        snomed_service=snomed_service,
    )
    await load_service.load_fields(fields_iter)

    if args.sync:
        sync_service = BigPictureSyncService()
        try:
            async with get_cursor() as cur:
                await sync_service.sync_fields(cur)
        finally:
            await sync_service.search.close()


async def _snomed_refresh() -> None:
    snomed_term_service = PostgresSnomedTermCacheService(BP_SNOMED_TABLE)
    snomed_service = SnomedService()
    logging.info("Refreshing SNOMED preferred terms.")
    await snomed_term_service.refresh(snomed_service)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bigpicture search admin CLI.")
    parser.add_argument(
        "--env-file",
        default=None,
        metavar="FILE",
        help="Path to a .env file to load environment variables from.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # load
    load_parser = subparsers.add_parser(
        "load",
        help="Load Bigpicture data from XML files into the database.",
    )
    load_parser.add_argument("directory", help="Directory to load from.")
    load_parser.add_argument(
        "--multi-dir",
        action="store_true",
        default=False,
        help=(
            "Treat directory as a parent directory containing multiple dataset "
            "subdirectories, instead of a single dataset directory."
        ),
    )
    load_parser.add_argument(
        "--load",
        action="store_true",
        default=False,
        help=(
            "Write extracted data to the database. Without this flag XMLs are "
            "parsed and validated but nothing is loaded to the database."
        ),
    )
    load_parser.add_argument(
        "--sync",
        action="store_true",
        default=False,
        help="Sync loaded data to OpenSearch after loading.",
    )
    load_parser.add_argument(
        "--c4gh-key-file",
        default=None,
        metavar="FILE",
        help="Path to a Crypt4GH private key file (.sec) for decrypting .c4gh files.",
    )
    load_parser.add_argument(
        "--c4gh-passphrase",
        default=None,
        metavar="PASSPHRASE",
        help="Passphrase for the Crypt4GH private key (omit for unprotected keys).",
    )

    # snomed
    snomed_parser = subparsers.add_parser("snomed", help="Managed SNOMED CT preferred terms cache.")
    snomed_subparsers = snomed_parser.add_subparsers(dest="snomed_command", required=True)
    snomed_subparsers.add_parser(
        "refresh",
        help=(
            "Refresh SNOMED CT preferred terms stored in the database. "
            "Run after a new SNOMED release to keep preferred terms current."
        ),
    )

    args = parser.parse_args()
    _setup(args.env_file)

    if args.command == "load":
        asyncio.run(_load(args))
    elif args.command == "snomed" and args.snomed_command == "refresh":
        asyncio.run(_snomed_refresh())
