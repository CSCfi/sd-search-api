"""SNOMED CT concept lookup via the Snowstorm terminology server."""

import httpx

from search_api.conf import common_config


# TODO: test
async def find_concept(
    term: str,
    ecl: str | None = None,
    branch: str = "MAIN",
) -> str | None:
    """Search for an active SNOMED CT concept by term.

    Args:
        term: Free-text search term or concept ID.
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

    async with httpx.AsyncClient(
        headers={"User-Agent": "sd-search-api/0.1", "Accept": "application/json"},
        follow_redirects=True,
        timeout=30.0,
    ) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

    items = data.get("items", [])
    if not items:
        return None

    return items[0]["conceptId"]
