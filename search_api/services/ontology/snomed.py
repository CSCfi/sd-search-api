"""SNOMED CT concept lookup via the Snowstorm terminology server."""

import asyncio
import logging
from pathlib import Path

import httpx
from aiocache import cached  # type: ignore[import-untyped]
from pydantic import BaseModel
from stdnum import verhoeff

from search_api.api.beacon.models import BeaconFilteringTerm
from search_api.conf import snowstorm_config as _snowstorm_config
from search_api.exceptions import SystemException
from search_api.services.ontology.service import OntologyService, normalise_term

_PAGE_SIZE = 1000
_CACHE_TTL = 60 * 60 * 24 * 30  # 30 days
_CONCEPT_ID_BATCH_SIZE = 50  # limit conceptIds to keep the request URL within limits
# Snowstorm answers a shorter term with 400 "Search term must have at least 3
# characters", so one is not sent rather than raising on the whole load or query.
_MIN_SEARCH_TERM_LENGTH = 3
_IMPORT_POLL_INTERVAL = 60.0  # seconds; an import can take hours to complete


class SnomedConcept(BaseModel):
    concept_id: str
    preferred_term: str


def _snowstorm_url() -> str:
    return _snowstorm_config().SNOWSTORM_URL


def _client(timeout: float | None = 30.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": "sd-search-api", "Accept": "application/json"},
        follow_redirects=True,
        timeout=timeout,
    )


# Two digits before the check digit define the kind of Snomed CT concept. Core and
# extension concepts are supported.
_CONCEPT_ID_PARTITIONS = frozenset({"00", "10"})
_CONCEPT_ID_MIN_LENGTH = 6
_CONCEPT_ID_MAX_LENGTH = 18


def is_concept_id(value: str) -> bool:
    """Return True if value is a well-formed SNOMED CT concept id.

    An SNOMED CT concept id is 6 to 18 digits and never starts with a zero. Its last
    three digits carry the partition identifier and a Verhoeff check digit.
    """
    if not value.isdigit() or value.startswith("0"):
        return False
    if not _CONCEPT_ID_MIN_LENGTH <= len(value) <= _CONCEPT_ID_MAX_LENGTH:
        return False
    if value[-3:-1] not in _CONCEPT_ID_PARTITIONS:
        return False
    return bool(verhoeff.is_valid(value))


@cached(ttl=_CACHE_TTL)
async def _fetch_concepts(
    term: str,
    ecl: str | None,
    branch: str,
    limit: int,
) -> list[SnomedConcept]:
    """Fetch active concepts whose preferred term or a synonym matches term.

    A concept id is never searched for here: it resolves to itself before the
    ontology is consulted, so only a term reaches this.

    Args:
        term: Free-text search term to match against concept preferred terms and synonyms.
        ecl: SNOMED CT Expression Constraint Language expression used to restrict results
             to a specific concept hierarchy. None searches across all concepts.
        branch: SNOMED CT branch path to search (e.g. ``"MAIN"``).
        limit: Maximum number of concepts to return.

    Returns:
        Active concepts matching term.
    """
    if len(term.strip()) < _MIN_SEARCH_TERM_LENGTH:
        return []

    url = f"{_snowstorm_url()}/{branch}/concepts"

    async with _client() as client:
        params: dict[str, str | int] = {
            "term": term,
            "activeFilter": "true",
            "limit": limit,
        }
        if ecl is not None:
            params["ecl"] = ecl
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return [
            SnomedConcept(
                concept_id=item["conceptId"], preferred_term=item["pt"]["term"]
            )
            for item in resp.json().get("items", [])
        ]


# The associations SNOMED CT records for a retired concept. SAME_AS and REPLACED_BY
# associations mean that an active equivalent concept exist. POSSIBLY_EQUIVALENT_TO
# is an uncertain equivalence, and ALTERNATIVE and REFERS_TO are not equivalences.
_EQUIVALENT_ASSOCIATIONS = ("SAME_AS", "REPLACED_BY")


@cached(ttl=_CACHE_TTL)
async def _fetch_concept(concept_id: str, branch: str) -> dict | None:
    """Fetch one whole concept, or None if there is no such concept.

    Read from the browser view rather than ``/{branch}/concepts/{id}``, which
    returns only the concept's own columns: no ``associationTargets``, no
    ``inactivationIndicator`` and no ``descriptions``. This is therefore the one
    call everything that reads a single concept shares, at the price of the axioms
    nothing here uses — 10.1 kB against the 3.5 kB of the descriptions alone for
    84499006, and 68.7 kB against 65.1 kB for 138875005, where the descriptions
    dominate either way.
    """
    async with _client() as client:
        resp = await client.get(
            f"{_snowstorm_url()}/browser/{branch}/concepts/{concept_id}"
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()


@cached(ttl=_CACHE_TTL)
async def _fetch_all_concepts(ecl: str, branch: str) -> list[SnomedConcept]:
    """Fetch active concepts matching the ecl expression.

    Args:
        ecl: SNOMED CT Expression Constraint Language expression used to restrict results
             to a specific concept hierarchy.
        branch: SNOMED CT branch path to search (e.g. ``"MAIN"``).

    Returns:
        All active concepts matching the ecl expression.
    """
    url = f"{_snowstorm_url()}/{branch}/concepts"

    concepts: list[SnomedConcept] = []
    search_after: str | None = None

    async with _client() as client:
        while True:
            params: dict[str, str | int] = {
                "ecl": ecl,
                "activeFilter": "true",
                "limit": _PAGE_SIZE,
            }
            if search_after:
                params["searchAfter"] = search_after
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

            items = data.get("items", [])
            for item in items:
                concepts.append(
                    SnomedConcept(
                        concept_id=item["conceptId"],
                        preferred_term=item["pt"]["term"],
                    )
                )

            search_after = data.get("searchAfter")
            if not items or not search_after:
                break

    return concepts


async def _fetch_descriptions(concept_id: str, branch: str) -> frozenset[str]:
    """Fetch a concept's active descriptions, normalised for matching.

    Args:
        concept_id: Concept ID whose descriptions are to be retrieved.
        branch: SNOMED CT branch path to search (e.g. ``"MAIN"``).

    Returns:
        The normalised term of every active description of the concept.
    """
    concept = await _fetch_concept(concept_id, branch)
    if concept is None:
        return frozenset()
    return frozenset(
        normalise_term(description["term"])
        for description in concept.get("descriptions", ())
        if description.get("active")
    )


async def import_snomed_release(release_file: Path, branch: str = "MAIN") -> None:
    """Import a new SNOMED CT release into Snowstorm.

    Creates an import job, uploads the release archive, and polls until the
    job completes. Mirrors the manual procedure in the README's "Import
    SNOMED release" section.

    Args:
        release_file: Path to the SNOMED CT release archive (e.g. a
            SnomedCT_InternationalRF2_*.zip file).
        branch: SNOMED CT branch path to import into. Defaults to ``"MAIN"``.

    Raises:
        SystemException: if the import job reports a failed status.
    """
    async with _client() as client:
        resp = await client.post(
            f"{_snowstorm_url()}/imports",
            json={
                "type": "SNAPSHOT",
                "branchPath": branch,
                "createCodeSystemVersion": True,
            },
        )
        resp.raise_for_status()
        import_id = resp.headers["location"].rsplit("/", 1)[-1]
    logging.info("Created Snowstorm import job '%s'.", import_id)

    logging.info("Uploading '%s'; this may take a while.", release_file)
    async with _client(timeout=None) as client:
        with release_file.open("rb") as f:
            resp = await client.post(
                f"{_snowstorm_url()}/imports/{import_id}/archive",
                files={"file": (release_file.name, f, "application/zip")},
            )
            resp.raise_for_status()

    logging.info("Waiting for import '%s' to complete.", import_id)
    while True:
        async with _client() as client:
            resp = await client.get(f"{_snowstorm_url()}/imports/{import_id}")
        if resp.status_code == 404:
            # The job becomes unavailable once done, so a
            # 404 here signals completion rather than an error.
            logging.info(
                "Import '%s' is no longer available; assuming completed.", import_id
            )
            return
        resp.raise_for_status()
        status = resp.json().get("status")
        if status == "COMPLETED":
            logging.info("Import '%s' completed.", import_id)
            return
        if status == "FAILED":
            raise SystemException(f"SNOMED CT import '{import_id}' failed.")
        logging.info("Import '%s' status: %s.", import_id, status)
        await asyncio.sleep(_IMPORT_POLL_INTERVAL)


class SnomedService(OntologyService):
    """SNOMED CT concept lookup service."""

    def __init__(self) -> None:
        pass

    def is_concept_id(self, value: str) -> bool:
        """Return True if value is a SNOMED CT concept ID."""
        return is_concept_id(value)

    async def find_concept(
        self,
        term: str,
        ecl: str | None = None,
        branch: str = "MAIN",
    ) -> SnomedConcept | None:
        """Return the best-matching active concept for the term, or None if not found.

        Args:
            term: Free-text search term.
            ecl: ECL expression to restrict the text search to a concept hierarchy.
                 None searches all concepts.
            branch: SNOMED CT branch path to search. Defaults to ``"MAIN"``

        Returns:
            The best-matching active concept, or None if no active concept matches.
        """
        concepts = await _fetch_concepts(term, ecl, branch, limit=1)
        return concepts[0] if concepts else None

    @staticmethod
    async def find_descendants(
        concept_id: str,
        branch: str = "MAIN",
    ) -> list[SnomedConcept]:
        """Return all active descendants of concept ID.

        Args:
            concept_id: Concept ID of the root node whose descendants are to be retrieved.
            branch: SNOMED CT branch path to search. Defaults to ``"MAIN"``

        Returns:
            Every active concept that is a  descendant of the concept ID.
        """
        return await _fetch_all_concepts(f"< {concept_id}", branch)

    async def replacement_concept_id(
        self, concept_id: str, branch: str = "MAIN"
    ) -> str | None:
        """Return the active concept replacing an inactive one, if exactly one is named.

        Retiring a concept removes its relationships, so a retired concept is a
        descendant of nothing and no subtree query reaches it.

        Answers None unless the concept is inactive and names exactly one active
        SAME_AS or REPLACED_BY concept.
        """
        concept = await _fetch_concept(concept_id, branch)
        if concept is None or concept.get("active"):
            return None

        targets = concept.get("associationTargets") or {}
        replacements = {
            target
            for association in _EQUIVALENT_ASSOCIATIONS
            for target in targets.get(association, ())
        }
        if len(replacements) != 1:
            return None

        replacement = replacements.pop()
        active_replacement = await _fetch_concept(replacement, branch)
        if active_replacement is None or not active_replacement.get("active"):
            return None
        return replacement

    async def get_preferred_terms(
        self,
        concept_ids: set[str],
        branch: str = "MAIN",
    ) -> dict[str, str]:
        """Return preferred terms for a set of known concept IDs.

        Args:
            concept_ids: SNOMED CT concept IDs to look up.
            branch: SNOMED CT branch path. Defaults to ``"MAIN"``.

        Returns:
            Mapping of concept ID to preferred term. IDs not found in
            Snowstorm are omitted.
        """
        if not concept_ids:
            return {}
        url = f"{_snowstorm_url()}/{branch}/concepts"
        result: dict[str, str] = {}
        ids_list = sorted(concept_ids)
        async with _client() as client:
            for i in range(0, len(ids_list), _CONCEPT_ID_BATCH_SIZE):
                batch = ids_list[i : i + _CONCEPT_ID_BATCH_SIZE]
                # No activeFilter to support retired concepts.
                params: dict[str, str | int] = {
                    "conceptIds": ",".join(batch),
                    "limit": len(batch),
                }
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                for item in resp.json().get("items", []):
                    result[item["conceptId"]] = item["pt"]["term"]
                    if not item.get("active"):
                        logging.warning(
                            "Concept %s ('%s') is inactive in SNOMED CT.",
                            item["conceptId"],
                            item["pt"]["term"],
                        )
        return result

    @staticmethod
    async def _describes(concept_id: str, value: str, branch: str = "MAIN") -> bool:
        """Return True if value is one of the concept's descriptions compared by normalise_term."""
        return normalise_term(value) in await _fetch_descriptions(concept_id, branch)

    async def _find_concept_ids(
        self, value: str, filtering_term: BeaconFilteringTerm
    ) -> set[str]:
        """Find a SNOMED CT concept ID for one filter value via Snowstorm.

        The value is searched within the field's restricted part of SNOMED CT, or
        all of it if the field is unrestricted. The best match is accepted
        only if the value is one of its descriptions.
        """
        concept = await self.find_concept(value, ecl=filtering_term.snomed_ecl)
        if concept is None:
            return set()
        if not await self._describes(concept.concept_id, value):
            return set()
        return {concept.concept_id}

    async def _find_descendant_ids(self, concept_ids: set[str]) -> set[str]:
        """Return every active descendant of the given concept IDs."""
        descendant_groups = await asyncio.gather(
            *(self.find_descendants(concept_id) for concept_id in concept_ids)
        )
        result: set[str] = set()
        for descendants in descendant_groups:
            result.update(d.concept_id for d in descendants)
        return result
