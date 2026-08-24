"""The models shared by the API layer."""

import pytest

from search_api.api.models import ValueCountsKey


def test_value_counts_key_distinguishes_scope():
    unrestricted = ValueCountsKey.of("sex")

    assert unrestricted != ValueCountsKey.of("sex", scope="clinical")
    assert unrestricted == ValueCountsKey.of("sex", scope=None)


def test_value_counts_key_cannot_be_changed():
    key = ValueCountsKey.of("sex")

    with pytest.raises(ValueError):
        key.field_id = "diagnosis"
