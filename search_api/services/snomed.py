"""SNOMED CT concept lookup via the Snowstorm terminology server."""

import httpx
from aiocache import cached
from pydantic import BaseModel

from search_api.conf import common_config

_PAGE_SIZE = 1000
_CACHE_TTL = 60 * 60 * 24 * 30  # 30 days


class SnomedConcept(BaseModel):
    concept_id: str
    term: str


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": "sd-search-api/0.1", "Accept": "application/json"},
        follow_redirects=True,
        timeout=30.0,
    )


def _is_concept_id(value: str) -> bool:
    """Return True if value is a SNOMED CT concept ID (digits only)."""
    return value.isdigit()


@cached(ttl=_CACHE_TTL)
async def find_concept(
    term: str,
    ecl: str | None = None,
    branch: str = "MAIN",
) -> str | None:
    """Return the conceptId for term, or ``None`` if not found.

    If term is a numeric SNOMED CT concept ID the concept is looked up directly.

    Args:
        term: Free-text search term or SNOMED CT concept ID.
        ecl: Optional ECL expression to restrict the search to a concept hierarchy.
        branch: SNOMED CT branch. Defaults to ``MAIN`` (International Edition).

    Returns:
        The concept id of the best-matching active concept, or None.
    """
    cfg = common_config()
    base_url = f"{cfg.SNOWSTORM_URL}/{branch}/concepts"

    async with _client() as client:
        if _is_concept_id(term):
            resp = await client.get(f"{base_url}/{term}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            return data["conceptId"] if data.get("active") else None
        else:
            params: dict[str, str | int] = {
                "term": term,
                "activeFilter": "true",
                "limit": 1,
            }
            if ecl is not None:
                params["ecl"] = ecl
            resp = await client.get(base_url, params=params)
            resp.raise_for_status()
            items = resp.json().get("items", [])
            return items[0]["conceptId"] if items else None


@cached(ttl=_CACHE_TTL)
async def list_descendants(
    concept_id: str,
    branch: str = "MAIN",
) -> list[SnomedConcept]:
    """Return all active descendants of the concept id.

    Args:
        concept_id: The SNOMED CT concept ID of the root node.
        branch: SNOMED CT branch to search. Defaults to ``MAIN`` (International Edition).

    Returns:
        All active descendants of the concept id.
    """
    cfg = common_config()
    url = f"{cfg.SNOWSTORM_URL}/{branch}/concepts"
    ecl = f"< {concept_id}"

    results: list[SnomedConcept] = []
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
                results.append(
                    SnomedConcept(
                        concept_id=item["conceptId"],
                        term=item["pt"]["term"],
                    )
                )

            offset += len(items)
            if offset >= data.get("total", 0):
                break

    return results
