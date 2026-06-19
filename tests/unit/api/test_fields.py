import textwrap
from pathlib import Path

import pytest

from search_api.api.fields import load_fields_config
from search_api.exceptions import ConfigurationException

_VALID_TERM = """\
filtering_terms:
  - id: title
    type: text
    scopes: [dataset]
    label: "Title"
    description: "The title"
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "fields.yaml"
    path.write_text(textwrap.dedent(text))
    return path


def test_load_valid(tmp_path):
    config = load_fields_config(_write(tmp_path, _VALID_TERM))
    assert [t.id for t in config.filtering_terms] == ["title"]
    assert config.non_filtering_fields == []


def test_missing_file(tmp_path):
    with pytest.raises(ConfigurationException, match="Cannot read config file"):
        load_fields_config(tmp_path / "does_not_exist.yaml")


def test_invalid_yaml_syntax(tmp_path):
    path = _write(tmp_path, "filtering_terms: [unclosed\n")
    with pytest.raises(ConfigurationException) as exc:
        load_fields_config(path)
    assert "YAML error in" in str(exc.value)
    assert "line" in str(exc.value)


def test_empty_file(tmp_path):
    with pytest.raises(ConfigurationException, match="Invalid config file"):
        load_fields_config(_write(tmp_path, ""))


def test_invalid_top_level_key(tmp_path):
    with pytest.raises(ConfigurationException, match="Invalid config file"):
        load_fields_config(_write(tmp_path, "- a\n- b\n"))


def test_unknown_top_level_key(tmp_path):
    path = _write(
        tmp_path,
        """\
        filtering_term:  # 'filtering_term' is a typo for 'filtering_terms'
          - id: title
            type: text
            scopes: [dataset]
            label: "Title"
            description: "The title"
        """,
    )
    with pytest.raises(ConfigurationException) as exc:
        load_fields_config(path)
    assert "filtering_term" in str(exc.value)


def test_unknown_key(tmp_path):
    path = _write(
        tmp_path,
        """\
        filtering_terms:
          - id: title
            type: text
            scopes: [dataset]
            labell: "Title"  # 'labell' is a typo for 'label'
            description: "The title"
        """,
    )
    with pytest.raises(ConfigurationException) as exc:
        load_fields_config(path)
    assert "filtering_terms[0]" in str(exc.value)
    assert "labell" in str(exc.value)


def test_invalid_type_value(tmp_path):
    path = _write(
        tmp_path,
        """\
        filtering_terms:
          - id: title
            type: invalid
            scopes: [dataset]
            label: "Title"
            description: "The title"
        """,
    )
    with pytest.raises(ConfigurationException) as exc:
        load_fields_config(path)
    assert "filtering_terms[0].type" in str(exc.value)
    assert "invalid" in str(exc.value)


def test_unquoted_numeric_ontology_concept_is_rejected(tmp_path):
    path = _write(
        tmp_path,
        """\
        filtering_terms:
          - id: animal_species
            type: ontology
            group: blocks
            scopes: [biological_being]
            label: "Species"
            description: "Species"
            ontology:
              id: SCTID
            ontologyConcept: 410607006
        """,
    )
    with pytest.raises(ConfigurationException) as exc:
        load_fields_config(path)
    assert "ontologyConcept" in str(exc.value)


def test_ontology_or_value_rejects_multivalued(tmp_path):
    path = _write(
        tmp_path,
        """\
        filtering_terms:
          - id: fixation_type
            type: ontologyOrValue
            group: blocks
            scopes: [specimen]
            label: "Fixation"
            description: "Fixation"
            ontology:
              id: SCTID
            ontologyConcept: "1388477003"
            multivalued: true
        """,
    )
    with pytest.raises(ConfigurationException, match="multivalued"):
        load_fields_config(path)
