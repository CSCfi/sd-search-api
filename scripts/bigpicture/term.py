"""Refresh SNOMED CT preferred terms stored in the database.

Resolves stored concept IDs against the current Snowstorm release and
updates the preferred term in the ``bp_snomed`` table. Run this after a
new SNOMED release to keep preferred terms current.
"""

import argparse
import asyncio
import logging
import warnings

from dotenv import load_dotenv

from search_api.api.bigpicture.models import BP_SNOMED_TABLE
from search_api.services.snomed import SnomedService
from search_api.services.snomed_term import PostgresSnomedTermCacheService


async def main() -> None:
    snomed_term_service = PostgresSnomedTermCacheService(BP_SNOMED_TABLE)
    snomed_service = SnomedService()
    logging.info("Refreshing SNOMED preferred terms.")
    await snomed_term_service.refresh(snomed_service)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Refresh SNOMED CT preferred terms stored in the database. "
        )
    )
    parser.add_argument(
        "--env-file",
        default=None,
        metavar="FILE",
        help="Path to a .env file to load environment variables from.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    warnings.filterwarnings("ignore", category=UserWarning, module="opensearchpy")

    if args.env_file:
        load_dotenv(args.env_file)

    asyncio.run(main())
