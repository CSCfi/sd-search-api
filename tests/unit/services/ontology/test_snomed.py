"""Unit tests for SNOMED OntologyService hooks called by ``prepare_ontology_filter`` template method."""

import pytest
from typing import NamedTuple
from unittest.mock import AsyncMock, patch

from search_api.api.bigpicture.models import BP_FILTERING_TERM_BY_ID
from search_api.exceptions import SystemException
from search_api.services.ontology import snomed as snomed_module
from search_api.services.ontology.snomed import (
    SnomedConcept,
    SnomedService,
    _fetch_descriptions,
    import_snomed_release,
)

ANIMAL_SPECIES_TERM = BP_FILTERING_TERM_BY_ID["animal_species"]
MOCK_SNOWSTORM_URL = "https://snowstorm.example"

# The SNOMED CT concepts these tests name.
CONCEPT_ID_HOMO_SAPIENS = "337915000"
CONCEPT_ID_ORGANISM = "410607006"  # the animal_species restriction's root
CONCEPT_ID_FROZEN_SECTION = "261014004"
CONCEPT_ID_NONEXISTENT = "999999006"


class _Replacement(NamedTuple):
    """A retired concept id and the active concept id replacing it."""

    retired: str
    active: str


# A concept id replacement named by the type of association.
REPLACEMENT_VIA_REPLACED_BY = _Replacement(retired="35917007", active="1187332001")
REPLACEMENT_VIA_SAME_AS = _Replacement(retired="84499006", active="409777003")

# Retired concepts with no active equivalent.
CONCEPT_ID_RETIRED_POSSIBLY_EQUIVALENT = "11111111000"
CONCEPT_ID_RETIRED_SEVERAL_REPLACEMENTS = "22222222000"
CONCEPT_ID_RETIRED_REPLACEMENT_ALSO_RETIRED = "33333333000"
CONCEPT_ID_RETIRED_REPLACEMENT = "44444444000"
CONCEPT_ID_RETIRED_NAMING_NOTHING = "55555555000"


class MockResponse:
    """Stands in for an httpx.Response."""

    def __init__(self, status_code=200, json_data=None, headers=None):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected status code {self.status_code}")

    def json(self):
        return self._json_data


class MockClient:
    """Stands in for the httpx.AsyncClient."""

    def __init__(self):
        self.post = AsyncMock()
        self.get = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def _concept(concept_id: str) -> SnomedConcept:
    return SnomedConcept(concept_id=concept_id, preferred_term=f"Term {concept_id}")


@pytest.fixture
def service() -> SnomedService:
    return SnomedService()


@pytest.mark.parametrize(
    "value,expected",
    [
        # Real concept ids, core and extension partitions.
        (CONCEPT_ID_ORGANISM, True),
        (CONCEPT_ID_HOMO_SAPIENS, True),
        (REPLACEMENT_VIA_REPLACED_BY.retired, True),
        # Not digits.
        ("Invalid", False),
        ("", False),
        # Invalid leading zeros.
        ("0410607006", False),
        # Invalid length (must be 6 to 18 digits long).
        ("41006", False),
        ("4106070060410607006", False),
        # Non-concept partition (two digits before last).
        ("410607016", False),
        ("12710026", False),
        # Invalid Verhoeff digit (last digit).
        ("410607007", False),
        ("337915001", False),
    ],
)
def test_is_well_formed(service, value, expected):
    assert service.is_well_formed(value) is expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("4", True),
        ("4106", True),
        (CONCEPT_ID_HOMO_SAPIENS, True),
        ("41006", True),
        ("410607007", True),
        ("0410", False),
        ("4106070060410607006", False),
        ("Homo", False),
        ("41o6", False),
        ("", False),
    ],
)
def test_is_concept_id_prefix(service, value, expected):
    assert service.is_concept_id_prefix(value) is expected


@pytest.mark.asyncio
async def test_find_concept_ids_passes_the_terms_ecl_to_snowstorm(service):
    service.find_concept = AsyncMock(return_value=_concept(CONCEPT_ID_HOMO_SAPIENS))

    with patch.object(SnomedService, "_describes", new=AsyncMock(return_value=True)):
        result = await service._find_concept_ids("Homo sapiens", ANIMAL_SPECIES_TERM)

    assert result == {CONCEPT_ID_HOMO_SAPIENS}
    service.find_concept.assert_awaited_once_with(
        "Homo sapiens", ecl=ANIMAL_SPECIES_TERM.snomed_ecl
    )


@pytest.mark.asyncio
async def test_find_concept_ids_resolves_to_nothing_when_no_concept_matches(service):
    service.find_concept = AsyncMock(return_value=None)

    result = await service._find_concept_ids("no match here", ANIMAL_SPECIES_TERM)

    assert result == set()


@pytest.mark.asyncio
async def test_find_concept_ids_rejects_a_match_the_value_does_not_describe(service):
    service.find_concept = AsyncMock(return_value=_concept(CONCEPT_ID_FROZEN_SECTION))

    with patch.object(SnomedService, "_describes", new=AsyncMock(return_value=False)):
        result = await service._find_concept_ids("Frozen", ANIMAL_SPECIES_TERM)

    assert result == set()


@pytest.mark.asyncio
async def test_find_descendant_ids_unions_the_descendants_of_every_concept_id(
    service,
):
    with patch.object(
        SnomedService,
        "find_descendants",
        # "222" descends from both concepts.
        new=AsyncMock(
            side_effect=[[_concept("111"), _concept("222")], [_concept("222")]]
        ),
    ) as find_descendants:
        result = await service._find_descendant_ids({CONCEPT_ID_ORGANISM, "888"})

    assert result == {"111", "222"}
    # Every concept id is looked up, and only those.
    assert sorted(call.args[0] for call in find_descendants.await_args_list) == [
        CONCEPT_ID_ORGANISM,
        "888",
    ]


@pytest.mark.asyncio
async def test_find_descendant_ids_resolves_to_nothing_for_a_leaf_concept(service):
    with patch.object(
        SnomedService, "find_descendants", new=AsyncMock(return_value=[])
    ):
        result = await service._find_descendant_ids({CONCEPT_ID_ORGANISM})

    assert result == set()


def import_job_created_response() -> MockResponse:
    return MockResponse(
        status_code=201,
        headers={"location": "https://snowstorm.example/imports/import-1"},
    )


@pytest.mark.asyncio
async def test_import_snomed_release(tmp_path):
    release_file = tmp_path / "release.zip"
    release_file.write_bytes(b"not a real release")

    # Creates a job, uploads the archive, then polls through RUNNING to COMPLETED.
    client = MockClient()
    client.post.side_effect = [
        import_job_created_response(),
        MockResponse(status_code=200),
    ]
    client.get.side_effect = [
        MockResponse(json_data={"status": "RUNNING"}),
        MockResponse(json_data={"status": "COMPLETED"}),
    ]
    with (
        patch.object(snomed_module, "_client", return_value=client),
        patch.object(snomed_module, "_snowstorm_url", return_value=MOCK_SNOWSTORM_URL),
        patch.object(snomed_module.asyncio, "sleep", new=AsyncMock()) as sleep,
    ):
        await import_snomed_release(release_file)

    create_job_call, upload_call = client.post.await_args_list
    assert create_job_call.args[0] == "https://snowstorm.example/imports"
    assert create_job_call.kwargs["json"] == {
        "type": "SNAPSHOT",
        "branchPath": "MAIN",
        "createCodeSystemVersion": True,
    }
    assert upload_call.args[0] == "https://snowstorm.example/imports/import-1/archive"
    assert client.get.await_count == 2
    sleep.assert_awaited_once()

    # A 404 on poll means the job is no longer available, i.e. is completed.
    client = MockClient()
    client.post.side_effect = [
        import_job_created_response(),
        MockResponse(status_code=200),
    ]
    client.get.side_effect = [MockResponse(status_code=404)]
    with (
        patch.object(snomed_module, "_client", return_value=client),
        patch.object(snomed_module, "_snowstorm_url", return_value=MOCK_SNOWSTORM_URL),
    ):
        await import_snomed_release(release_file)  # does not raise

    # A FAILED status raises.
    client = MockClient()
    client.post.side_effect = [
        import_job_created_response(),
        MockResponse(status_code=200),
    ]
    client.get.side_effect = [MockResponse(json_data={"status": "FAILED"})]
    with (
        patch.object(snomed_module, "_client", return_value=client),
        patch.object(snomed_module, "_snowstorm_url", return_value=MOCK_SNOWSTORM_URL),
    ):
        with pytest.raises(SystemException):
            await import_snomed_release(release_file)


def _mock_fetch_concept(concept_id: str, active: bool, **associations) -> dict:
    """A concept returned by the browser view, with its historical associations."""
    return {
        "conceptId": concept_id,
        "active": active,
        "associationTargets": {kind: list(ids) for kind, ids in associations.items()},
    }


@pytest.mark.parametrize(
    "concept_id,expected",
    [
        (REPLACEMENT_VIA_REPLACED_BY.retired, REPLACEMENT_VIA_REPLACED_BY.active),
        (REPLACEMENT_VIA_SAME_AS.retired, REPLACEMENT_VIA_SAME_AS.active),
        (CONCEPT_ID_ORGANISM, None),  # active, so nothing to replace
        (CONCEPT_ID_RETIRED_POSSIBLY_EQUIVALENT, None),
        (CONCEPT_ID_RETIRED_SEVERAL_REPLACEMENTS, None),
        (CONCEPT_ID_RETIRED_REPLACEMENT_ALSO_RETIRED, None),
        (CONCEPT_ID_RETIRED_NAMING_NOTHING, None),
        (CONCEPT_ID_NONEXISTENT, None),
    ],
)
@pytest.mark.asyncio
async def test_replacement_concept_id(service, monkeypatch, concept_id, expected):
    """Test concept id replacement with mock _fetch_concept."""

    _mock_fetch_concept_values = {
        CONCEPT_ID_ORGANISM: _mock_fetch_concept(CONCEPT_ID_ORGANISM, active=True),
        # Retired with active replacement.
        REPLACEMENT_VIA_REPLACED_BY.retired: _mock_fetch_concept(
            REPLACEMENT_VIA_REPLACED_BY.retired,
            False,
            REPLACED_BY=[REPLACEMENT_VIA_REPLACED_BY.active],
        ),
        REPLACEMENT_VIA_REPLACED_BY.active: _mock_fetch_concept(
            REPLACEMENT_VIA_REPLACED_BY.active, active=True
        ),
        REPLACEMENT_VIA_SAME_AS.retired: _mock_fetch_concept(
            REPLACEMENT_VIA_SAME_AS.retired,
            False,
            SAME_AS=[REPLACEMENT_VIA_SAME_AS.active],
        ),
        REPLACEMENT_VIA_SAME_AS.active: _mock_fetch_concept(
            REPLACEMENT_VIA_SAME_AS.active, active=True
        ),
        # Retired with no active replacement.
        CONCEPT_ID_RETIRED_POSSIBLY_EQUIVALENT: _mock_fetch_concept(
            CONCEPT_ID_RETIRED_POSSIBLY_EQUIVALENT,
            False,
            POSSIBLY_EQUIVALENT_TO=[CONCEPT_ID_ORGANISM],
        ),
        CONCEPT_ID_RETIRED_SEVERAL_REPLACEMENTS: _mock_fetch_concept(
            CONCEPT_ID_RETIRED_SEVERAL_REPLACEMENTS,
            False,
            REPLACED_BY=[CONCEPT_ID_ORGANISM, REPLACEMENT_VIA_SAME_AS.active],
        ),
        CONCEPT_ID_RETIRED_REPLACEMENT_ALSO_RETIRED: _mock_fetch_concept(
            CONCEPT_ID_RETIRED_REPLACEMENT_ALSO_RETIRED,
            False,
            REPLACED_BY=[CONCEPT_ID_RETIRED_REPLACEMENT],
        ),
        # Retired with a retired replacement.
        CONCEPT_ID_RETIRED_REPLACEMENT: _mock_fetch_concept(
            CONCEPT_ID_RETIRED_REPLACEMENT, active=False
        ),
        CONCEPT_ID_RETIRED_NAMING_NOTHING: _mock_fetch_concept(
            CONCEPT_ID_RETIRED_NAMING_NOTHING, active=False
        ),
    }

    async def mock_fetch_concept(_concept_id: str, _: str) -> dict | None:
        return _mock_fetch_concept_values.get(_concept_id)

    monkeypatch.setattr(
        "search_api.services.ontology.snomed._fetch_concept", mock_fetch_concept
    )

    assert await service.replacement_concept_id(concept_id) == expected


@pytest.mark.asyncio
async def test_describes(service, monkeypatch):
    """Test concept description with mock _fetch_concept."""

    calls: list[str] = []

    async def mock_fetch_concept(concept_id: str, _: str) -> dict | None:
        calls.append(concept_id)
        return {
            "conceptId": concept_id,
            "active": True,
            "descriptions": [
                {"term": "Homo sapiens", "active": True},
                {"term": "  HUMAN  ", "active": True},
                {"term": "Retired synonym", "active": False},
            ],
        }

    monkeypatch.setattr(
        "search_api.services.ontology.snomed._fetch_concept", mock_fetch_concept
    )

    assert await SnomedService._describes(CONCEPT_ID_HOMO_SAPIENS, "human") is True
    assert (
        await SnomedService._describes(CONCEPT_ID_HOMO_SAPIENS, "Retired synonym")
        is False
    )
    assert calls == [CONCEPT_ID_HOMO_SAPIENS, CONCEPT_ID_HOMO_SAPIENS]


@pytest.mark.asyncio
async def test_describes_invalid_concept_id(service, monkeypatch):
    """Test description for invalid concept id with mock _fetch_concept."""

    async def mock_fetch_concept(concept_id: str, _: str) -> dict | None:
        return None

    monkeypatch.setattr(
        "search_api.services.ontology.snomed._fetch_concept", mock_fetch_concept
    )

    assert await _fetch_descriptions(CONCEPT_ID_NONEXISTENT, "MAIN") == frozenset()
    assert await SnomedService._describes(CONCEPT_ID_NONEXISTENT, "anything") is False
    assert (
        await SnomedService._describes(CONCEPT_ID_NONEXISTENT, CONCEPT_ID_NONEXISTENT)
        is False
    )


@pytest.mark.asyncio
async def test_is_within_restriction_ontology_restriction(service, monkeypatch):
    """Test that is_selected_by_ecl is called."""
    calls: list[tuple[str, str, str]] = []

    async def mock_is_selected_by_ecl(concept_id: str, ecl: str, branch: str) -> bool:
        calls.append((concept_id, ecl, branch))
        return True

    monkeypatch.setattr(
        "search_api.services.ontology.snomed._is_selected_by_ecl",
        mock_is_selected_by_ecl,
    )

    assert (
        await service.is_within_restriction(
            CONCEPT_ID_HOMO_SAPIENS, ANIMAL_SPECIES_TERM
        )
        is True
    )
    assert calls == [(CONCEPT_ID_HOMO_SAPIENS, ANIMAL_SPECIES_TERM.snomed_ecl, "MAIN")]


@pytest.mark.asyncio
async def test_is_within_restriction_no_ontology_restriction(service, monkeypatch):
    """Test that is_selected_by_ecl is not called."""
    monkeypatch.setattr(
        "search_api.services.ontology.snomed._is_selected_by_ecl",
        AsyncMock(side_effect=AssertionError("Snowstorm must not be asked")),
    )
    unrestricted = ANIMAL_SPECIES_TERM.model_copy(update={"ontologyRestriction": None})

    assert (
        await service.is_within_restriction(CONCEPT_ID_HOMO_SAPIENS, unrestricted)
        is True
    )
