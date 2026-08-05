"""Search admin CLI."""

import argparse
import asyncio
import json
import logging
import warnings
from pathlib import Path

from dotenv import load_dotenv

import search_api
from search_api.api.deployments import DOMAINS
from search_api.api.domain import Domain
from search_api.api.opensearch.index_generator import OpenSearchIndexGeneratorService
from search_api.api.opensearch.services import create_index, create_search
from search_api.conf import deployment_config
from search_api.exceptions import SystemException
from search_api.services.load import LoadService
from search_api.services.sync import SyncService
from search_api.database.document import count_documents
from search_api.database.repository import get_cursor
from search_api.api.beacon.models import SNOMED_ONTOLOGY_ID
from search_api.services.cached_ontology import PostgresOntologyStore
from search_api.services.send import SEND_ONTOLOGY_ID, SendOntologySource
from search_api.services.snomed import SnomedService
from search_api.services.ontology_term import (
    PostgresOntologyTermCacheService,
    create_term_caches,
)


def _index_path(domain: Domain) -> Path:
    return (
        Path(search_api.__file__).parent
        / "opensearch"
        / domain.name
        / f"{domain.opensearch_index}.json"
    )


def _setup(env_file: str | None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    warnings.filterwarnings("ignore", category=UserWarning, module="opensearchpy")
    if env_file:
        load_dotenv(env_file)


async def _load(domain: Domain, args: argparse.Namespace) -> None:
    options = domain.loader.parse_load_options(args)
    docs_iter = domain.loader.extract(options)

    if not args.load:
        logging.info("Extracting without writing to the database.")
        count = 0
        for doc in docs_iter:
            logging.info("Would load document %s.", doc.id)
            count += 1
        logging.info("%d document(s) extracted without loading them.", count)
        return

    logging.info("Loading documents into the database.")
    load_service = LoadService(
        create_term_caches(domain.ontology_ids), domain.filtering_terms
    )
    await load_service.store_documents(docs_iter)

    if args.sync:
        sync_service = SyncService(domain.opensearch_index)
        try:
            async with get_cursor() as cur:
                await sync_service.sync_fields(cur)
        finally:
            await sync_service.search.close()


async def _clear(domain: Domain, args: argparse.Namespace) -> None:
    if deployment_config().DEPLOYMENT_ENV == "prod":
        raise SystemException("This command is not available in production.")

    def _confirm_clear(_doc_count: int) -> bool:
        try:
            answer = input(
                f"All documents ({_doc_count}) will be deleted from database and OpenSearch Index '{domain.opensearch_index}'.\n"
                f"Type '{args.group}' to confirm, or anything else to abort: "
            )
        except EOFError:
            return False
        return answer == args.group

    sync_service = SyncService(domain.opensearch_index)
    try:
        async with get_cursor() as cur:
            doc_count = await count_documents(cur)

        if not _confirm_clear(doc_count):
            logging.info("Aborted, nothing was deleted.")
            return

        async with get_cursor() as cur:
            await sync_service.delete_all_documents(cur)
    finally:
        await sync_service.search.close()


async def _snomed_refresh() -> None:
    snomed_term_service = PostgresOntologyTermCacheService(
        ontology_id=SNOMED_ONTOLOGY_ID
    )
    snomed_service = SnomedService()
    logging.info("Refreshing SNOMED preferred terms.")
    await snomed_term_service.refresh(snomed_service)


async def _send_refresh() -> None:
    store = PostgresOntologyStore(SEND_ONTOLOGY_ID)
    source = SendOntologySource()
    stored = await store.read()
    fetched = await source.fetch()

    if stored is not None and not source.is_newer(fetched.version, stored.version):
        logging.info(
            "SEND ontology is already up to date (stored version '%s', fetched '%s').",
            stored.version,
            fetched.version,
        )
        return

    changed = stored is None or fetched.sha256 != stored.sha256
    await store.write(fetched)
    logging.info(
        "Updated SEND ontology to version '%s' with '%d' concepts%s",
        fetched.version,
        len(fetched.concepts),
        "." if changed else " (content unchanged).",
    )


def _generate_index(domain: Domain) -> None:
    body = OpenSearchIndexGeneratorService(domain.opensearch_fields).generate()
    path = _index_path(domain)
    path.write_text(json.dumps(body, indent=2) + "\n")
    logging.info("Wrote OpenSearch index to %s.", path)


async def _create_index(domain: Domain) -> None:
    body = OpenSearchIndexGeneratorService(domain.opensearch_fields).generate()
    search = create_search()
    try:
        await create_index(search, domain.opensearch_index, body)
        logging.info("Created OpenSearch index %s.", domain.opensearch_index)
    finally:
        await search.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Search admin CLI.")
    parser.add_argument(
        "--env-file",
        default=None,
        metavar="FILE",
        help="Path to a .env file to load environment variables from.",
    )

    # The first positional selects a command group. Most groups are deployments;
    # each registers its own deployment-specific load flags. SNOMED is
    # a cross-cutting group that operates on the shared preferred-terms cache and
    # is not tied to any deployment.
    groups = parser.add_subparsers(dest="group", required=True)

    for name, domain in sorted(DOMAINS.items()):
        commands = groups.add_parser(
            name, help=f"{name} deployment commands."
        ).add_subparsers(dest="command", required=True)

        # load
        load_parser = commands.add_parser(
            "load", help="Load data from source files into the database."
        )
        load_parser.add_argument(
            "--load",
            action="store_true",
            default=False,
            help=(
                "Write extracted data to the database. Without this flag sources "
                "are parsed and validated but nothing is loaded to the database."
            ),
        )
        load_parser.add_argument(
            "--sync",
            action="store_true",
            default=False,
            help="Sync loaded data to OpenSearch after loading.",
        )
        # Deployment-specific load flags.
        domain.loader.add_load_options(load_parser)

        # generate-index
        commands.add_parser(
            "generate-index", help="Generate the OpenSearch JSON index."
        )

        # create-index
        commands.add_parser(
            "create-index",
            help=(
                "Create the OpenSearch index in the cluster from the generated "
                "mapping. Required once per environment before the first --sync; "
                "fails if the index already exists."
            ),
        )

        # clear
        commands.add_parser(
            "clear", help="Delete all data from the database and the OpenSearch index."
        )

    # snomed (deployment-independent)
    snomed_commands = groups.add_parser(
        "snomed", help="Manage the shared SNOMED CT preferred terms cache."
    ).add_subparsers(dest="snomed_command", required=True)
    snomed_commands.add_parser(
        "refresh",
        help=(
            "Refresh SNOMED CT preferred terms stored in the database. "
            "Run after a new SNOMED release to keep preferred terms current."
        ),
    )

    # send (deployment-independent)
    send_commands = groups.add_parser(
        "send", help="Manage the SEND controlled terminology concept table."
    ).add_subparsers(dest="send_command", required=True)
    send_commands.add_parser(
        "refresh",
        help=(
            "Refresh SEND ontology cached in the database. "
            "Run after a new SEND release to keep SEND ontology current."
        ),
    )

    args = parser.parse_args()
    _setup(args.env_file)

    if args.group == "snomed":
        if args.snomed_command == "refresh":
            asyncio.run(_snomed_refresh())
    elif args.group == "send":
        if args.send_command == "refresh":
            asyncio.run(_send_refresh())
    else:
        domain = DOMAINS[args.group]
        if args.command == "load":
            asyncio.run(_load(domain, args))
        elif args.command == "generate-index":
            _generate_index(domain)
        elif args.command == "create-index":
            asyncio.run(_create_index(domain))
        elif args.command == "clear":
            asyncio.run(_clear(domain, args))
