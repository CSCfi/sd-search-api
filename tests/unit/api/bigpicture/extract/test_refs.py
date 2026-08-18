from lxml import etree

from search_api.api.bigpicture.extract.models import ObjectIds, ObjectKey
from search_api.api.bigpicture.extract.refs import object_keys


def test_object_ids_id():
    """The id is the accession when present, otherwise the mandatory alias."""
    assert ObjectIds(alias="1", accession="slide_1").id == "slide_1"
    assert ObjectIds(alias="1").id == "1"


def test_object_ids_keys():
    """Both the accession and the alias are keys, tagged by kind so an
    accession is never confused with an alias."""
    assert ObjectIds(alias="1", accession="slide_1").keys == [
        ObjectKey(kind="alias", value="1"),
        ObjectKey(kind="accession", value="slide_1"),
    ]
    assert ObjectIds(alias="1").keys == [ObjectKey(kind="alias", value="1")]


def test_object_keys_from_element():
    assert object_keys(etree.fromstring('<SLIDE alias="1" accession="slide_1"/>')) == [
        ObjectKey(kind="accession", value="slide_1"),
        ObjectKey(kind="alias", value="1"),
    ]
    assert object_keys(etree.fromstring('<SLIDE alias="1"/>')) == [
        ObjectKey(kind="alias", value="1"),
    ]
    assert object_keys(etree.fromstring('<SLIDE accession="slide_1"/>')) == [
        ObjectKey(kind="accession", value="slide_1"),
    ]


def test_object_keys_from_object_ids():
    objects = [ObjectIds(alias="1", accession="slide_1"), ObjectIds(alias="2")]
    assert object_keys(objects) == [
        ObjectKey(kind="alias", value="1"),
        ObjectKey(kind="accession", value="slide_1"),
        ObjectKey(kind="alias", value="2"),
    ]
