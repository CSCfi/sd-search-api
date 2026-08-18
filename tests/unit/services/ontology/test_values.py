import pytest

from search_api.api.opensearch.models import OpenSearchField, OpenSearchFieldValue
from search_api.exceptions import SystemException
from search_api.services.ontology.values import OntologyValueWithBinding


def test_valid_ontology_value():
    ontology_value = OntologyValueWithBinding.model_construct(
        value=OpenSearchFieldValue.model_construct(
            field=OpenSearchField(id="animal_species", type="ontology"),
            value=("337915000", "meaning"),
        ),
    )

    # Value must be concept id and meaning tuple.
    assert ontology_value.concept_id == "337915000"
    assert ontology_value.meaning == "meaning"


def test_invalid_ontology_value():
    ontology_value = OntologyValueWithBinding.model_construct(
        value=OpenSearchFieldValue.model_construct(
            field=OpenSearchField(id="animal_species", type="ontology"),
            value="337915000",
        ),
    )

    # Value must be concept id and meaning tuple.
    with pytest.raises(SystemException, match="Invalid .* for an ontology field"):
        _ = ontology_value.concept_id
