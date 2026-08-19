"""The Bigpicture deployment as a Domain."""

import argparse
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from search_api.api.bigpicture.models import (
    BP_BEACON_ID,
    BP_BEACON_NAME,
    BP_DOMAIN_NAME,
    BP_FILTERING_GROUPS,
    BP_FILTERING_QUALIFIERS,
    BP_FILTERING_SCOPES,
    BP_FILTERING_TERMS,
    BP_NON_FILTERING_FIELDS,
    BP_OPENSEARCH_INDEX,
    BP_SCHEMAS,
    BigpictureBeaconDatasetResultSetsResponse,
    BigpictureBeaconImageResultSetsResponse,
)
from search_api.api.bigpicture.ai import (
    BP_AI_ASSISTANT_DESCRIPTION,
    BP_AI_IMAGE_RESULT_INSTRUCTIONS,
    BP_AI_DATASET_RESULT_INSTRUCTIONS,
    BigpictureAIImageSearchResult,
    BigpictureAIDatasetSearchResult,
)
from search_api.api.bigpicture.opensearch import (
    BigpictureDatasetBeaconService,
    BigpictureImageBeaconService,
)
from search_api.api.beacon.services import OpenSearchBeaconService
from search_api.api.domain import BeaconQueryEndpoint, Domain, Loader
from search_api.api.opensearch.models import ExtractedDocument
from search_api.api.bigpicture.extract import extract_documents


@dataclass(frozen=True)
class BigpictureLoadOptions:
    """Options for loading Bigpicture XML datasets."""

    root: str
    single_dir: bool
    c4gh_private_key_file: str | None
    c4gh_passphrase: str | None


def _add_load_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("directory", help="Directory to load from.")
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


def _parse_load_options(args: argparse.Namespace) -> BigpictureLoadOptions:
    return BigpictureLoadOptions(
        root=args.directory,
        single_dir=not args.multi_dir,
        c4gh_private_key_file=args.c4gh_key_file,
        c4gh_passphrase=args.c4gh_passphrase,
    )


def _extract(options: BigpictureLoadOptions) -> Iterator[ExtractedDocument]:
    return extract_documents(
        root=options.root,
        single_dir=options.single_dir,
        c4gh_private_key_file=options.c4gh_private_key_file,
        c4gh_passphrase=options.c4gh_passphrase,
    )


BP_LOADER = Loader(
    add_load_options=_add_load_options,
    parse_load_options=_parse_load_options,
    extract=_extract,
)

BP_DOMAIN = Domain(
    name=BP_DOMAIN_NAME,
    opensearch_index=BP_OPENSEARCH_INDEX,
    index_file=Path(__file__).parent / "index" / f"{BP_OPENSEARCH_INDEX}.json",
    filtering_terms=BP_FILTERING_TERMS,
    filtering_groups=BP_FILTERING_GROUPS,
    filtering_scopes=BP_FILTERING_SCOPES,
    filtering_qualifiers=BP_FILTERING_QUALIFIERS,
    non_filtering_fields=BP_NON_FILTERING_FIELDS,
    loader=BP_LOADER,
    beacon_service_factory=lambda search: OpenSearchBeaconService(
        search,
        BP_OPENSEARCH_INDEX,
        BP_FILTERING_TERMS,
        BP_FILTERING_SCOPES,
        BP_FILTERING_QUALIFIERS,
    ),
    query_endpoints=[
        BeaconQueryEndpoint(
            path="/datasets",
            beacon_service_factory=lambda search: BigpictureDatasetBeaconService(
                search,
                BP_OPENSEARCH_INDEX,
                BP_FILTERING_TERMS,
                BP_FILTERING_SCOPES,
                BP_FILTERING_QUALIFIERS,
            ),
            result_sets_response_model=BigpictureBeaconDatasetResultSetsResponse,
            ai_assistant_description=BP_AI_ASSISTANT_DESCRIPTION,
            ai_result_model=BigpictureAIDatasetSearchResult,
            ai_result_instructions=BP_AI_DATASET_RESULT_INSTRUCTIONS,
        ),
        BeaconQueryEndpoint(
            path="/images",
            beacon_service_factory=lambda search: BigpictureImageBeaconService(
                search,
                BP_OPENSEARCH_INDEX,
                BP_FILTERING_TERMS,
                BP_FILTERING_SCOPES,
                BP_FILTERING_QUALIFIERS,
            ),
            result_sets_response_model=BigpictureBeaconImageResultSetsResponse,
            ai_assistant_description=BP_AI_ASSISTANT_DESCRIPTION,
            ai_result_model=BigpictureAIImageSearchResult,
            ai_result_instructions=BP_AI_IMAGE_RESULT_INSTRUCTIONS,
        ),
    ],
    beacon_id=BP_BEACON_ID,
    beacon_name=BP_BEACON_NAME,
    schemas=BP_SCHEMAS,
)
