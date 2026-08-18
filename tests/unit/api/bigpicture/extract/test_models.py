from search_api.api.bigpicture.extract.models import (
    OBSERVATION_CANDIDATE,
    OBSERVATION_CONFIRMED,
    OBSERVATION_QUALIFIER,
    NESTED_GROUPS,
)
from search_api.api.bigpicture.models import BP_FILTERING_QUALIFIERS


def test_observation_qualifier_matches_config():
    qualifier = next(
        q for q in BP_FILTERING_QUALIFIERS if q.id == OBSERVATION_QUALIFIER
    )
    assert set(qualifier.values) == {OBSERVATION_CONFIRMED, OBSERVATION_CANDIDATE}
    assert set(qualifier.groups) <= set(NESTED_GROUPS)
