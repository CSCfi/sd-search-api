"""SNOMED CT concept lookup via the Snowstorm terminology server."""

import asyncio

import httpx
from aiocache import cached  # type: ignore[import-untyped]
from pydantic import BaseModel

from search_api.api.beacon.models import BeaconFilteringTerm
from search_api.conf import snowstorm_config as _snowstorm_config
from search_api.services.ontology import OntologyService, normalise_term

_PAGE_SIZE = 1000
_CACHE_TTL = 60 * 60 * 24 * 30  # 30 days
_CONCEPT_ID_BATCH_SIZE = 50  # limit conceptIds to keep the request URL within limits


class SnomedConcept(BaseModel):
    concept_id: str
    preferred_term: str


def _snowstorm_url() -> str:
    return _snowstorm_config().SNOWSTORM_URL


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": "sd-search-api", "Accept": "application/json"},
        follow_redirects=True,
        timeout=30.0,
    )


def is_concept_id(value: str) -> bool:
    """Return True if value is a SNOMED CT concept ID (digits only)."""
    return value.isdigit()


async def _fetch_concepts(
    term: str,
    ecl: str | None,
    branch: str,
    limit: int,
) -> list[SnomedConcept]:
    """Fetch active concepts matching term.

    If term is concept ID it is looked up directly.

    Args:
        term: Free-text search term to match against concept preferred terms and synonyms,
              or concept ID for a direct lookup.
        ecl: SNOMED CT Expression Constraint Language expression used to restrict results
             to a specific concept hierarchy. None searches across all concepts.
             Ignored when term is a concept ID.
        branch: SNOMED CT branch path to search (e.g. ``"MAIN"``).
        limit: Maximum number of concepts to return.

    Returns:
        Active concepts matching term.
    """
    base_url = f"{_snowstorm_url()}/{branch}/concepts"

    async with _client() as client:
        if is_concept_id(term):
            resp = await client.get(f"{base_url}/{term}")
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            data = resp.json()
            if not data.get("active"):
                return []
            return [
                SnomedConcept(
                    concept_id=data["conceptId"], preferred_term=data["pt"]["term"]
                )
            ]

        params: dict[str, str | int] = {
            "term": term,
            "activeFilter": "true",
            "limit": limit,
        }
        if ecl is not None:
            params["ecl"] = ecl
        resp = await client.get(base_url, params=params)
        resp.raise_for_status()
        return [
            SnomedConcept(
                concept_id=item["conceptId"], preferred_term=item["pt"]["term"]
            )
            for item in resp.json().get("items", [])
        ]


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


@cached(ttl=_CACHE_TTL)
async def _fetch_descriptions(concept_id: str, branch: str) -> frozenset[str]:
    """Fetch a concept's active descriptions, normalised for matching.

    Args:
        concept_id: Concept ID whose descriptions are to be retrieved.
        branch: SNOMED CT branch path to search (e.g. ``"MAIN"``).

    Returns:
        The normalised term of every active description of the concept.
    """
    url = f"{_snowstorm_url()}/{branch}/descriptions"
    async with _client() as client:
        resp = await client.get(
            url, params={"conceptIds": concept_id, "limit": _PAGE_SIZE}
        )
        resp.raise_for_status()
    return frozenset(
        normalise_term(item["term"])
        for item in resp.json().get("items", [])
        if item.get("active")
    )


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

        If term is a concept ID it is looked up directly.

        Args:
            term: Free-text search term or concept ID.
            ecl: ECL expression to restrict the text search to a concept hierarchy.
                 Ignored when term is a concept ID. None searches all concepts.
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
                params: dict[str, str | int] = {
                    "conceptIds": ",".join(batch),
                    "activeFilter": "true",
                    "limit": len(batch),
                }
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                for item in resp.json().get("items", []):
                    result[item["conceptId"]] = item["pt"]["term"]
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
