"""Search admin CLI."""

import argparse
import asyncio
import json
import logging
import os
import warnings
from pathlib import Path

from dotenv import load_dotenv

import search_api
from search_api.api.deployments import DOMAINS
from search_api.api.domain import Domain
from search_api.api.opensearch.index_generator import OpenSearchIndexGeneratorService
from search_api.api.opensearch.client import create_search
from search_api.api.opensearch.index import create_index
from search_api.exceptions import SystemException
from search_api.services.load import LoadService, extraction_logs
from search_api.services.fetch import DocumentSource
from search_api.services.sync import SyncService
from search_api.database.document import count_documents, reset_synced_at
from search_api.database.document_log import (
    delete_all_document_logs,
    log_document_log,
)
from search_api.database.repository import get_cursor
from search_api.database.load import (
    delete_load_marker,
    insert_load_history,
    read_load_marker,
    write_load_marker,
)
from search_api.database.terms_cache import delete_all_terms
from search_api.api.beacon.models import SNOMED_ONTOLOGY_ID
from search_api.services.ontology.cache.source import OntologySource
from search_api.services.ontology.cache.store import OntologyCacheStore
from search_api.services.ontology.service import get_ontology_service
from search_api.services.ontology.send import SEND_ONTOLOGY_ID, SendOntologySource
from search_api.services.ontology.term_cache import create_term_caches
from search_api.services.ontology.snomed import import_snomed_release


def _schema_path(name: str) -> Path:
    return Path(search_api.__file__).parent / "database" / "schema" / f"{name}.sql"


def _require_non_production() -> None:
    """Refuse a destructive command outside a development environment.

    :raises SystemException: if DEPLOYMENT_ENV is production.
    """
    if os.getenv("DEPLOYMENT_ENV", "dev") == "prod":
        raise SystemException("This command is not available in production.")


def _confirm(what_will_happen: str, expected: str) -> bool:
    """Ask the operator to type ``expected`` before something destructive happens."""
    try:
        answer = input(
            f"{what_will_happen}\n"
            f"Type '{expected}' to confirm, or anything else to abort: "
        )
    except EOFError:
        return False
    return answer == expected


def _setup(env_file: str | None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    warnings.filterwarnings("ignore", category=UserWarning, module="opensearchpy")
    if env_file:
        load_dotenv(env_file)


async def _load(domain: Domain, args: argparse.Namespace) -> None:
    """Load documents from a local source."""

    await _read_documents(domain, domain.local_source, args, root=args.directory)


async def _fetch(domain: Domain, args: argparse.Namespace) -> None:
    """Load documents from a remote source."""

    await _read_documents(domain, domain.remote_source, args)


async def _read_documents(
    domain: Domain,
    source: DocumentSource | None,
    args: argparse.Namespace,
    root: str | None = None,
) -> None:
    """
    Read documents for indexing.

    :param domain: The deployment configuration.
    :param source: The source to read, or None if the deployment declares none.
    :param args: The command arguments.
    :param root: The directory to read, if supported by the document source.
    """

    if source is None:
        raise SystemException(
            f"The {domain.name} deployment has no source to {args.command}."
        )

    marker = await _load_marker(args)
    load_service = (
        None
        if args.dry_run
        else LoadService(
            create_term_caches(domain.ontology_ids),
            domain.filtering_terms,
            domain.filtering_scopes,
            domain.filtering_qualifiers,
            domain.replace_concepts,
        )
    )

    count = 0
    read_marker = None
    async for itr in source.read(root, marker):
        count += len(itr.documents)

        if load_service is None:
            for doc in itr.documents:
                logging.info("Would load document %s.", doc.id)
                for log in extraction_logs(doc):
                    log_document_log(log)
            continue

        logging.info("Loading %d document(s).", len(itr.documents))
        await load_service.store_documents(iter(itr.documents))
        # Update incremental load marker.
        read_marker = itr.marker

    logging.info(
        "%d document(s) %s.",
        count,
        "read without loading them" if load_service is None else "loaded",
    )

    if read_marker is not None:
        await write_load_marker(read_marker)
        await insert_load_history(read_marker)

    if args.sync and not args.dry_run:
        await _sync(domain)


async def _load_marker(args: argparse.Namespace) -> str | None:
    """
    Return the stored incremental load position.

    :param args: The command arguments.
    :return: The stored incremental load position, or None to read all documents.
    """

    if args.full:
        if not args.dry_run:
            # The marker is deleted before loading, so that an interrupted load
            # does not leave the old marker.
            await delete_load_marker()
            logging.info("Full load requested, removing incremental load marker.")
        return None

    marker = await read_load_marker()
    if marker is None:
        logging.info("No incremental load marker. Loading all documents.")
        return None

    logging.info("Loading documents after the incremental marker %s.", marker)
    return marker


async def _reset(domain: Domain) -> None:
    """Remove the incremental load marker, so the next load reads everything again."""

    await delete_load_marker()
    logging.info(
        "Removed the incremental load marker of the %s deployment. "
        "The next load will be a full one.",
        domain.name,
    )


async def _sync(domain: Domain) -> None:
    """Sync the documents pending sync to OpenSearch."""
    sync_service = SyncService(domain.opensearch_index)
    try:
        async with get_cursor() as cur:
            await sync_service.sync_fields(cur)
    finally:
        await sync_service.search.close()


async def _clear(domain: Domain, args: argparse.Namespace) -> None:
    """Remove all documents and the preferred terms cached for them."""

    _require_non_production()

    sync_service = SyncService(domain.opensearch_index)
    try:
        doc_count = await count_documents()

        if not _confirm(
            f"All documents ({doc_count}) will be deleted from database and "
            f"OpenSearch Index '{domain.opensearch_index}', together with any "
            f"associated data.",
            args.group,
        ):
            logging.info("Aborted, nothing was deleted.")
            return

        async with get_cursor() as cur:
            await sync_service.delete_all_documents(cur)
            log_count = await delete_all_document_logs(cur)
            logging.info("Deleted %d document log row(s).", log_count)

        # The terms are cleared after the documents.
        term_count = await delete_all_terms()
        logging.info("Deleted %d cached preferred term(s).", term_count)

        # The incremental load marker is cleared.
        await delete_load_marker()
        logging.info("Deleted the incremental load marker.")
    finally:
        await sync_service.search.close()


async def _recreate(domain: Domain, args: argparse.Namespace) -> None:
    """Drop and recreate the OpenSearch index and the database schema."""
    _require_non_production()

    if not _confirm(
        f"The OpenSearch index '{domain.opensearch_index}' and the database "
        f"schema will be dropped and recreated. All documents and "
        f"cached ontology terms will be lost.",
        args.group,
    ):
        logging.info("Aborted, nothing was dropped.")
        return

    async with get_cursor() as cur:
        for _name in ("drop", "create"):
            await cur.execute(_schema_path(_name).read_text())  # type: ignore[arg-type]
    logging.info("Recreated the database schema.")

    await _replace_index(domain)


async def _replace_index(domain: Domain) -> None:
    """Drop the OpenSearch index if it exists and create it from the generated mapping."""
    body = OpenSearchIndexGeneratorService(domain.opensearch_fields).generate()
    search = create_search()
    try:
        if await search.indices.exists(index=domain.opensearch_index):
            await search.indices.delete(index=domain.opensearch_index)
            logging.info("Deleted OpenSearch index %s.", domain.opensearch_index)
        await create_index(search, domain.opensearch_index, body)
        logging.info("Created OpenSearch index %s.", domain.opensearch_index)
    finally:
        await search.close()


async def _recreate_index(domain: Domain, args: argparse.Namespace) -> None:
    """Recreate the OpenSearch index and mark every document as pending sync."""
    _require_non_production()

    if not _confirm(
        f"The OpenSearch index '{domain.opensearch_index}' will be dropped and "
        f"recreated from the generated mapping. The documents stay in the database "
        f"but without synced_at, so 'sync' restores the index.",
        args.group,
    ):
        logging.info("Aborted, nothing was dropped.")
        return

    await _replace_index(domain)

    async with get_cursor() as cur:
        count = await reset_synced_at(cur)
    logging.info("Marked %d document(s) as pending sync.", count)


async def _import_snomed_release(release_file: Path) -> None:
    """Import a SNOMED CT release into the shared Snowstorm."""
    await import_snomed_release(release_file)


async def _update_cached_ontology(ontology_id: str, source: OntologySource) -> None:
    """Update an ontology cached in the database, if the source has a newer one."""
    store = OntologyCacheStore(ontology_id)
    stored = await store.read()
    fetched = await source.fetch()

    if stored is not None and not source.is_newer(fetched.version, stored.version):
        logging.info(
            "%s ontology is already up to date (stored version '%s', fetched '%s').",
            ontology_id,
            stored.version,
            fetched.version,
        )
        return

    changed = stored is None or fetched.sha256 != stored.sha256
    await store.write(fetched)
    logging.info(
        "Updated %s ontology to version '%s' with '%d' concepts%s",
        ontology_id,
        fetched.version,
        len(fetched.concepts),
        "." if changed else " (content unchanged).",
    )


_CACHED_ONTOLOGY_SOURCES: dict[str, OntologySource] = {
    SEND_ONTOLOGY_ID: SendOntologySource(),
}


_ONTOLOGY_IDS: dict[str, str] = {
    "snomed": SNOMED_ONTOLOGY_ID,
    "send": SEND_ONTOLOGY_ID,
}


async def _refresh_ontology(ontology_id: str) -> None:
    """Refresh the preferred terms this deployment caches for one ontology."""
    source = _CACHED_ONTOLOGY_SOURCES.get(ontology_id)
    if source is not None:
        await _update_cached_ontology(ontology_id, source)

    # Initialised after the update so the terms are refreshed against it.
    ontology = get_ontology_service(ontology_id)
    await ontology.init()

    logging.info("Refreshing %s preferred terms.", ontology_id)
    await create_term_caches([ontology_id])[ontology_id].refresh(ontology)


def _generate_index(domain: Domain) -> None:
    body = OpenSearchIndexGeneratorService(domain.opensearch_fields).generate()
    path = domain.index_file
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

    groups = parser.add_subparsers(dest="group", required=True)

    for name, domain in sorted(DOMAINS.items()):
        commands = groups.add_parser(
            name, help=f"{name} deployment commands."
        ).add_subparsers(dest="command", required=True)

        # Deployment specific options.
        #

        load_parser = commands.add_parser(
            "load", help="Load documents from a directory."
        )
        load_parser.add_argument(
            "directory",
            help="Directory to load the documents from.",
        )

        fetch_parser = commands.add_parser(
            "fetch",
            help=("Load documents from a remote source."),
        )

        for source_parser in (load_parser, fetch_parser):
            source_parser.add_argument(
                "--full",
                action="store_true",
                default=False,
                help="Reset the incremental load marker and load all documents.",
            )
            source_parser.add_argument(
                "--dry-run",
                action="store_true",
                default=False,
                help=(
                    "Read and validate the documents, reporting what would be loaded, "
                    "without writing anything to the database."
                ),
            )
            source_parser.add_argument(
                "--sync",
                action="store_true",
                default=False,
                help="Sync loaded data to OpenSearch after loading.",
            )

        commands.add_parser(
            "reset",
            help="Reset the incremental load marker, so the next load is a full one.",
        )

        commands.add_parser(
            "sync",
            help=(
                "Sync the documents pending sync to OpenSearch. Recreating the "
                "index marks every document pending, so nothing else is needed "
                "to refill it."
            ),
        )
        index_commands = commands.add_parser(
            "index", help="OpenSearch index commands."
        ).add_subparsers(dest="index_command", required=True)
        index_commands.add_parser(
            "generate",
            help=(
                "Generate the OpenSearch index mapping from the field definitions "
                "and write it to the deployment's index file."
            ),
        )
        index_commands.add_parser(
            "create",
            help=(
                "Create the OpenSearch index from the generated mapping. Required "
                "once per environment before the first sync. Fails if the index "
                "already exists."
            ),
        )
        index_commands.add_parser(
            "recreate",
            help=(
                "Drop the OpenSearch index and create it from the generated "
                "mapping. Needed after a mapping change. Marks every document as "
                "pending, so follow it with 'sync'. Not available in production."
            ),
        )
        refresh_parser = commands.add_parser(
            "refresh",
            help=(
                "Refresh what this deployment caches for an ontology. The "
                "preferred terms, and the ontology itself when the database "
                "caches it whole."
            ),
        )
        refresh_parser.add_argument(
            "ontology",
            # The ontologies this deployment uses.
            choices=[
                name
                for name, ontology_id in _ONTOLOGY_IDS.items()
                if ontology_id in domain.ontology_ids
            ],
            help="The ontology to refresh.",
        )

        # clear
        commands.add_parser(
            "clear",
            help=(
                "Delete all documents from the database and the OpenSearch index, "
                "together with every cached preferred term. Not available in "
                "production."
            ),
        )

        # recreate
        commands.add_parser(
            "recreate",
            help=(
                "Drop and recreate the OpenSearch index and the database schema, "
                "discarding all data. Not available in production."
            ),
        )

    # Snomed options.
    #

    snomed_commands = groups.add_parser(
        "snomed", help="SNOMED CT, served by one Snowstorm shared by all deployments."
    ).add_subparsers(dest="snomed_command", required=True)
    snomed_import_parser = snomed_commands.add_parser(
        "import",
        help=(
            "Import a release into Snowstorm. Follow up with each "
            "deployment's 'refresh snomed'."
        ),
    )
    snomed_import_parser.add_argument(
        "--release-file",
        type=Path,
        required=True,
        metavar="FILE",
        help=(
            "Path to a SNOMED CT release archive (e.g. "
            "SnomedCT_InternationalRF2_PRODUCTION_<date>.zip)."
        ),
    )

    args = parser.parse_args()
    _setup(args.env_file)

    if args.group == "snomed":
        if args.snomed_command == "import":
            asyncio.run(_import_snomed_release(args.release_file))
        raise SystemExit(0)

    domain = DOMAINS[args.group]
    if args.command == "fetch":
        asyncio.run(_fetch(domain, args))
    elif args.command == "load":
        asyncio.run(_load(domain, args))
    elif args.command == "reset":
        asyncio.run(_reset(domain))
    elif args.command == "sync":
        asyncio.run(_sync(domain))
    elif args.command == "index":
        if args.index_command == "generate":
            _generate_index(domain)
        elif args.index_command == "create":
            asyncio.run(_create_index(domain))
        elif args.index_command == "recreate":
            asyncio.run(_recreate_index(domain, args))
    elif args.command == "refresh":
        asyncio.run(_refresh_ontology(_ONTOLOGY_IDS[args.ontology]))
    elif args.command == "clear":
        asyncio.run(_clear(domain, args))
    elif args.command == "recreate":
        asyncio.run(_recreate(domain, args))
