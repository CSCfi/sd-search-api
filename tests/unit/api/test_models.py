"""The models shared by the API layer."""

import pytest

from search_api.api.models import ValueCountsKey


def test_value_counts_key_ignores_the_order_qualifier_values_are_given_in():
    """A qualifier restricts a set, so the order is not part of what was asked for."""
    one_way = ValueCountsKey.of(
        "diagnosis", qualifiers={"observation": ["confirmed", "candidate"]}
    )
    the_other = ValueCountsKey.of(
        "diagnosis", qualifiers={"observation": ["candidate", "confirmed"]}
    )

    assert one_way == the_other


def test_value_counts_key_distinguishes_scope_and_qualifiers():
    unrestricted = ValueCountsKey.of("sex")

    assert unrestricted != ValueCountsKey.of("sex", scope="clinical")
    assert unrestricted != ValueCountsKey.of(
        "sex", qualifiers={"observation": ["confirmed"]}
    )
    assert unrestricted == ValueCountsKey.of("sex", qualifiers=None)


def test_value_counts_key_cannot_be_changed():
    key = ValueCountsKey.of("sex")

    with pytest.raises(ValueError):
        key.field_id = "diagnosis"
