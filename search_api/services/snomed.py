"""SNOMED CT concept lookup via the Snowstorm terminology server."""

import asyncio

import httpx
from aiocache import cached  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from search_api.api.beacon.models import BeaconFilteringTerm
from search_api.conf import snowstorm_config as _snowstorm_config
from search_api.services.ontology import OntologyService

_PAGE_SIZE = 1000
_CACHE_TTL = 60 * 60 * 24 * 30  # 30 days
_SYNONYM_TYPE = "SYNONYM"
_SYNONYM_BATCH_SIZE = 50  # limit conceptIds to keep request URL within length limits


class SnomedConcept(BaseModel):
    concept_id: str
    preferred_term: str
    matched_term: str | None = None
    synonyms: list[str] = Field(default_factory=list, exclude=True)


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


async def _fetch_synonyms(
    concept_ids: list[str],
    branch: str,
    client: httpx.AsyncClient,
) -> dict[str, list[str]]:
    """Fetch active synonyms for the given concept IDs.

    Args:
        concept_ids: concept IDs to fetch synonyms for.
        branch: SNOMED CT branch path (e.g. ``"MAIN"``).
        client: Shared HTTP client.

    Returns:
        Mapping of concept ID to list of active synonym terms.
    """
    url = f"{_snowstorm_url()}/{branch}/descriptions"
    result: dict[str, list[str]] = {cid: [] for cid in concept_ids}

    for i in range(0, len(concept_ids), _SYNONYM_BATCH_SIZE):
        batch = concept_ids[i : i + _SYNONYM_BATCH_SIZE]
        offset = 0
        while True:
            params: dict[str, str | int] = {
                "conceptIds": ",".join(batch),
                "limit": _PAGE_SIZE,
                "offset": offset,
            }
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", [])
            for item in items:
                if item.get("active") and item.get("type") == _SYNONYM_TYPE:
                    cid = item.get("conceptId")
                    if cid in result:
                        result[cid].append(item["term"])
            offset += len(items)
            if offset >= data.get("total", 0):
                break

    return result


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
        Active concepts matching term, with synonyms populated.
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
            concepts = [
                SnomedConcept(
                    concept_id=data["conceptId"], preferred_term=data["pt"]["term"]
                )
            ]
        else:
            params: dict[str, str | int] = {
                "term": term,
                "activeFilter": "true",
                "limit": limit,
            }
            if ecl is not None:
                params["ecl"] = ecl
            resp = await client.get(base_url, params=params)
            resp.raise_for_status()
            concepts = [
                SnomedConcept(
                    concept_id=item["conceptId"], preferred_term=item["pt"]["term"]
                )
                for item in resp.json().get("items", [])
            ]

        concept_ids = [c.concept_id for c in concepts]
        synonyms = await _fetch_synonyms(concept_ids, branch, client)

    return [
        c.model_copy(update={"synonyms": synonyms.get(c.concept_id, [])})
        for c in concepts
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

    concepts: list[dict[str, str]] = []
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
                    {
                        "concept_id": item["conceptId"],
                        "preferred_term": item["pt"]["term"],
                    }
                )

            search_after = data.get("searchAfter")
            if not items or not search_after:
                break

        concept_ids = [concept["concept_id"] for concept in concepts]
        synonyms = await _fetch_synonyms(concept_ids, branch, client)

    return [
        SnomedConcept(
            concept_id=concept["concept_id"],
            preferred_term=concept["preferred_term"],
            synonyms=synonyms.get(concept["concept_id"], []),
        )
        for concept in concepts
    ]


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

    async def search_concepts(
        self,
        term: str,
        ecl: str | None = None,
        branch: str = "MAIN",
        limit: int = 10,
    ) -> list[SnomedConcept]:
        """Search for active concepts matching the term.

        Args:
            term: Free-text search term matched against concept preferred terms and synonyms,
            or concept ID.
            ecl: ECL expression to restrict results to a concept hierarchy.
                None searches across all concepts.
            branch: SNOMED CT branch path to search. Defaults to ``"MAIN"``
            limit: Maximum number of results to return.

        Returns:
            Matching concepts ordered by Snowstorm relevance score.
        """
        return await _fetch_concepts(term, ecl, branch, limit)

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
            for i in range(0, len(ids_list), _SYNONYM_BATCH_SIZE):
                batch = ids_list[i : i + _SYNONYM_BATCH_SIZE]
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
    async def get_concepts(
        concept_ids: set[str] | None,
        ecl: str,
        branch: str = "MAIN",
    ) -> dict[str, SnomedConcept]:
        """Return a mapping of concept IDs to SnomedConcept.

        Args:
            concept_ids: Concept IDs to map. When None, all concepts in the hierarchy are returned.
            ecl: ECL expression defining the concept hierarchy.
            branch: SNOMED CT branch path to search. Defaults to ``"MAIN"``

        Returns:
            Mapping of concept IDs to SnomedConcept. Concept IDs not found are excluded.
        """
        all_concepts = await _fetch_all_concepts(ecl, branch)
        if concept_ids is None:
            return {c.concept_id: c for c in all_concepts}
        return {c.concept_id: c for c in all_concepts if c.concept_id in concept_ids}

    async def suggest_concepts(
        self,
        term: str,
        ecl: str,
        branch: str = "MAIN",
        limit: int = 10,
        indexed_concept_ids: set[str] | None = None,
    ) -> list[SnomedConcept]:
        """Return autocomplete suggestions for term within a concept hierarchy.

        Filters an in-memory cached list of all concepts limited by the ecl expression.

        Args:
            term: Partial text to match against concept preferred terms and synonyms.
            ecl: ECL expression defining the concept hierarchy to search within.
            branch: SNOMED CT branch path to search. Defaults to ``"MAIN"``
            limit: Maximum number of suggestions to return.
            indexed_concept_ids: When provided, restricts results to these concept IDs.

        Returns:
            Matching concepts. matched_term is set to the synonym that caused the
            match when the preferred term did not match. None when the preferred
            term matched.
        """
        all_concepts = await _fetch_all_concepts(ecl, branch)
        term_lower = term.lower()

        def _matches(text: str) -> bool:
            return any(word.startswith(term_lower) for word in text.lower().split())

        results: list[SnomedConcept] = []
        for concept in all_concepts:
            if (
                indexed_concept_ids is not None
                and concept.concept_id not in indexed_concept_ids
            ):
                continue
            if _matches(concept.preferred_term):
                results.append(concept.model_copy(update={"matched_term": None}))
            else:
                for synonym in concept.synonyms:
                    if _matches(synonym):
                        results.append(
                            concept.model_copy(update={"matched_term": synonym})
                        )
                        break
            if len(results) >= limit:
                break

        return results

    async def _resolve_concept_ids(
        self, value: str, filtering_term: BeaconFilteringTerm
    ) -> set[str]:
        """Resolve one filter value to a SNOMED CT concept ID via Snowstorm."""
        concept = await self.find_concept(value, ecl=filtering_term.snomed_ecl)
        return {concept.concept_id} if concept is not None else set()

    async def _resolve_descendant_ids(self, concept_ids: set[str]) -> set[str]:
        """Return every active descendant of the given concept IDs."""
        descendant_groups = await asyncio.gather(
            *(self.find_descendants(concept_id) for concept_id in concept_ids)
        )
        result: set[str] = set()
        for descendants in descendant_groups:
            result.update(d.concept_id for d in descendants)
        return result
