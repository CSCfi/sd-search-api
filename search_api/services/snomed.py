"""SNOMED CT concept lookup via the Snowstorm terminology server."""

import httpx
from aiocache import cached  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from search_api.conf import common_config

_PAGE_SIZE = 1000
_CACHE_TTL = 60 * 60 * 24 * 30  # 30 days
_SYNONYM_TYPE = "SYNONYM"
_SYNONYM_BATCH_SIZE = 100


class SnomedConcept(BaseModel):
    concept_id: str
    term: str
    matched_term: str | None = None
    synonyms: list[str] = Field(default_factory=list, exclude=True)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": "sd-search-api/0.1", "Accept": "application/json"},
        follow_redirects=True,
        timeout=30.0,
    )


def _is_concept_id(value: str) -> bool:
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
    cfg = common_config()
    url = f"{cfg.SNOWSTORM_URL}/{branch}/descriptions"
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
        Active concepts matching term.
    """
    cfg = common_config()
    base_url = f"{cfg.SNOWSTORM_URL}/{branch}/concepts"

    async with _client() as client:
        if _is_concept_id(term):
            resp = await client.get(f"{base_url}/{term}")
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            data = resp.json()
            if not data.get("active"):
                return []
            return [
                SnomedConcept(concept_id=data["conceptId"], term=data["pt"]["term"])
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
        items = resp.json().get("items", [])

    return [
        SnomedConcept(concept_id=item["conceptId"], term=item["pt"]["term"])
        for item in items
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
    cfg = common_config()
    url = f"{cfg.SNOWSTORM_URL}/{branch}/concepts"

    concepts: list[dict[str, str]] = []
    offset = 0

    async with _client() as client:
        while True:
            params: dict[str, str | int] = {
                "ecl": ecl,
                "activeFilter": "true",
                "limit": _PAGE_SIZE,
                "offset": offset,
            }
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

            items = data.get("items", [])
            for item in items:
                concepts.append(
                    {"concept_id": item["conceptId"], "term": item["pt"]["term"]}
                )

            offset += len(items)
            if offset >= data.get("total", 0):
                break

        concept_ids = [concept["concept_id"] for concept in concepts]
        synonyms = await _fetch_synonyms(concept_ids, branch, client)

    return [
        SnomedConcept(
            concept_id=concept["concept_id"],
            term=concept["term"],
            synonyms=synonyms.get(concept["concept_id"], []),
        )
        for concept in concepts
    ]


@cached(ttl=_CACHE_TTL)
async def find_concept(
    term: str,
    ecl: str | None = None,
    branch: str = "MAIN",
) -> str | None:
    """Return the concept ID for the term, or None if not found.

    If term is a concept ID it is looked up directly.

    Args:
        term: Free-text search term or concept ID.
        ecl: ECL expression to restrict the text search to a concept hierarchy.
             Ignored when term is a concept ID. None searches all concepts.
        branch: SNOMED CT branch path to search. Defaults to ``"MAIN"``

    Returns:
        The concept id of the best-matching active concept, or None if no
        active concept matches.
    """
    concepts = await _fetch_concepts(term, ecl, branch, limit=1)
    return concepts[0].concept_id if concepts else None


@cached(ttl=_CACHE_TTL)
async def search_concepts(
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


async def list_descendants(
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


async def autocomplete_concepts(
    term: str,
    ecl: str,
    branch: str = "MAIN",
    limit: int = 10,
    prefix_match: bool = True,
) -> list[SnomedConcept]:
    """Return autocomplete suggestions for term within a concept hierarchy.

    Filters an in-memory cached list of all concepts limited by the ecl expression.

    Args:
        term: Partial text to match against concept preferred terms and synonyms.
        ecl: ECL expression defining the concept hierarchy to search within.
        branch: SNOMED CT branch path to search. Defaults to ``"MAIN"``
        limit: Maximum number of suggestions to return.
        prefix_match: When True, matches concepts where any word in the preferred
                      term or a synonym starts with term. When False, matches concepts
                      where term appears anywhere in the preferred term or a synonym.

    Returns:
        Matching concepts. matched_term is set to the synonym that caused the
        match when the preferred term did not match. None when the preferred
        term matched.
    """
    all_concepts = await _fetch_all_concepts(ecl, branch)
    term_lower = term.lower()

    def _matches(text: str) -> bool:
        text_lower = text.lower()
        if prefix_match:
            return any(word.startswith(term_lower) for word in text_lower.split())
        return term_lower in text_lower

    results: list[SnomedConcept] = []
    for concept in all_concepts:
        if _matches(concept.term):
            results.append(concept.model_copy(update={"matched_term": None}))
        else:
            for synonym in concept.synonyms:
                if _matches(synonym):
                    results.append(concept.model_copy(update={"matched_term": synonym}))
                    break
        if len(results) >= limit:
            break

    return results
