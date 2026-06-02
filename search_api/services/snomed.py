"""SNOMED CT concept lookup via the Snowstorm terminology server."""

import httpx
from pydantic import BaseModel

from search_api.conf import common_config

_PAGE_SIZE = 1000


class SnomedConcept(BaseModel):
    concept_id: str
    term: str


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": "sd-search-api/0.1", "Accept": "application/json"},
        follow_redirects=True,
        timeout=30.0,
    )


async def find_concept(
    term: str,
    ecl: str | None = None,
    branch: str = "MAIN",
) -> str | None:
    """Search for an active SNOMED CT concept by term.

    Args:
        term: Free-text search term.
        ecl: Optional ECL expression to restrict the search to a concept hierarchy.
             Example: ``"<< 410607006"`` searches within Organism and all descendants.
        branch: SNOMED CT branch to search. Defaults to ``MAIN`` (International Edition).

    Returns:
        The ``conceptId`` of the best-matching active concept, or ``None`` if no
        match is found.
    """
    cfg = common_config()
    url = f"{cfg.SNOWSTORM_URL}/{branch}/concepts"
    params: dict[str, str | int] = {
        "term": term,
        "activeFilter": "true",
        "limit": 1,
    }
    if ecl is not None:
        params["ecl"] = ecl

    async with _client() as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

    items = data.get("items", [])
    if not items:
        return None

    return items[0]["conceptId"]


async def list_descendants(
    concept_id: str,
    branch: str = "MAIN",
) -> list[SnomedConcept]:
    """Return all active descendants of the concept id.

    Args:
        concept_id: The SNOMED CT concept ID of the root node.
        branch: SNOMED CT branch to search. Defaults to ``MAIN`` (International Edition).

    Returns:
        All active descendants of the concept id..
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
