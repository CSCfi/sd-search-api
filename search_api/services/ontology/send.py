"""SEND concept lookup via ontology_cache table.

The ontology is retrieved from a tab-delimited file with code lists
and codes e.g.:

Code	Codelist Code	Codelist Extensible (Yes/No)	Codelist Name	CDISC Submission Value	CDISC Synonym(s)	CDISC Definition	NCI Preferred Term
C158118		Yes	Age Estimation Method Response	AGESMETH	Age Estimation Method Response	Terminology about age estimation.	CDISC SEND Age Estimation Method Response Terminology
C158324	C158118		Age Estimation Method Response	ANIMAL RECORDS		From animal records.	Animal Record Information

Code list rows do not have "Codelist" value. Code rows refer to a code
list using "Codelist Code". Rows for codes may be repeated, once for
each code list the code belongs to.

The version of the SEND ontology is extracted from the release or
modification date (whichever is more recent) in another text
file e.g.:

Quarter    Release Date    Modified date    Reason
Q1 2026    2026-03-27      2026-03-30      ...
"""

import csv
import hashlib
import io
import re
from datetime import date

import httpx

from search_api.exceptions import SystemException
from search_api.services.ontology.cached import (
    BootstrapCachedOntologySource,
    CachedOntologySource,
    PostgresOntologyStore,
    CachedOntologyConcept,
    CachedOntology,
)

SEND_ONTOLOGY_ID = "SEND"

ONTOLOGY_SEND_URL = "https://evs.nci.nih.gov/ftp1/CDISC/SEND/SEND%20Terminology.txt"
ONTOLOGY_SEND_VERSION_URL = (
    "https://evs.nci.nih.gov/ftp1/CDISC/SEND/SEND%20Publication%20Date%20Stamp.txt"
)

_COLUMN_CODE = "Code"
_COLUMN_PARENT_CODE = "Codelist Code"
_COLUMN_CODELIST_NAME = "Codelist Name"
_COLUMN_SUBMISSION_VALUE = "CDISC Submission Value"
_COLUMN_SYNONYMS = "CDISC Synonym(s)"
_COLUMN_PREFERRED_TERM = "NCI Preferred Term"


def parse_send_ontology(content: bytes) -> list[CachedOntologyConcept]:
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")), delimiter="\t")

    parent_ids: dict[str, set[str]] = {}  # code id -> code list ids
    preferred_terms_and_synonyms: dict[
        str, tuple[str, set[str]]
    ] = {}  # code id -> (preferred_term, synonyms)

    for row in reader:
        code_id = row[_COLUMN_CODE].strip()
        is_code_list = not row[_COLUMN_PARENT_CODE].strip()
        # For code list rows, "NCI Preferred Term" is a verbose auto-label.
        # Use "Codelist Name" instead.
        preferred_term = (
            row[_COLUMN_CODELIST_NAME].strip()
            if is_code_list
            else row[_COLUMN_PREFERRED_TERM].strip()
        )
        if not code_id or not preferred_term:
            raise SystemException(
                f"SEND ontology row is missing {_COLUMN_CODE} or {_COLUMN_PREFERRED_TERM}: {row}"
            )

        parent_id = row[_COLUMN_PARENT_CODE].strip()
        if parent_id:
            parent_ids.setdefault(code_id, set()).add(parent_id)

        _, synonyms = preferred_terms_and_synonyms.setdefault(
            code_id, (preferred_term, set())
        )
        if is_code_list:
            # Keep verbose auto-label as a synonym.
            synonyms.add(row[_COLUMN_PREFERRED_TERM].strip())
        # Add synonyms.
        for synonym in row[_COLUMN_SYNONYMS].split(";"):
            synonym = synonym.strip()
            if synonym:
                synonyms.add(synonym)
        submission_value = row[_COLUMN_SUBMISSION_VALUE].strip()
        if submission_value:
            synonyms.add(submission_value)
        synonyms.discard(preferred_term)

    return [
        CachedOntologyConcept(
            concept_id=code_id,
            preferred_term=preferred_term,
            synonyms=frozenset(synonyms),
            parent_ids=frozenset(parent_ids.get(code_id, ())),
        )
        for code_id, (preferred_term, synonyms) in preferred_terms_and_synonyms.items()
    ]


def parse_send_ontology_version(content: bytes) -> str:
    # Version is maximum of release or modified  date.

    lines = [line for line in content.decode("utf-8").splitlines() if line.strip()]
    dates: list[date] = []
    for line in lines[1:]:  # first line is the header
        columns = re.split(r"\t+", line.strip())
        if len(columns) < 2:
            continue
        release_date = columns[1]
        modified_date = columns[2] if len(columns) >= 4 else None
        dates.append(date.fromisoformat(modified_date or release_date))
    if not dates:
        raise SystemException("No release or modified date found.")
    return max(dates).isoformat()


class SendOntologySource(CachedOntologySource):
    """Fetches SEND controlled terminology from the URLs NCI EVS publishes it at."""

    async def fetch(self) -> CachedOntology:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            version_resp = await client.get(ONTOLOGY_SEND_VERSION_URL)
            version_resp.raise_for_status()
            data_resp = await client.get(ONTOLOGY_SEND_URL)
            data_resp.raise_for_status()

        version = parse_send_ontology_version(version_resp.content)
        sha256 = hashlib.sha256(data_resp.content).hexdigest()
        concepts = parse_send_ontology(data_resp.content)
        return CachedOntology(version=version, sha256=sha256, concepts=concepts)

    def is_newer(self, version: str, other: str) -> bool:
        """A SEND version is a release or modification date."""
        return date.fromisoformat(version) > date.fromisoformat(other)


def send_ontology_source() -> BootstrapCachedOntologySource:
    return BootstrapCachedOntologySource(
        PostgresOntologyStore(SEND_ONTOLOGY_ID), SendOntologySource()
    )
