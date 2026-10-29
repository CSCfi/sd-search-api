from io import BytesIO
from pathlib import Path

import pytest
from lxml import etree

from search_api.utils.xml import (
    get_xml_value,
    get_xml_values,
    parse_xml,
    validate_xml,
)


def test_parse_xml_with_string():
    xml = "<root><a>1</a></root>"
    tree = parse_xml(xml)
    assert tree.getroot().tag == "root"
    assert tree.getroot()[0].text == "1"


def test_parse_xml_with_bytes():
    xml = b"<root><a>1</a></root>"
    tree = parse_xml(xml)
    assert tree.getroot().tag == "root"
    assert tree.getroot()[0].text == "1"


def test_parse_xml_with_file_path(tmp_path: Path):
    xml_file = tmp_path / "test.xml"
    xml_file.write_text("<root><a>1</a></root>", encoding="utf-8")

    tree = parse_xml(xml_file)
    assert tree.getroot().tag == "root"
    assert tree.getroot()[0].text == "1"


def test_parse_xml_with_io_bytes():
    xml_io = BytesIO(b"<root><a>1</a></root>")
    tree = parse_xml(xml_io)
    assert tree.getroot().tag == "root"
    assert tree.getroot()[0].text == "1"


def test_validate_xml(tmp_path: Path):
    schema_file = "test.xsd"

    schema = b"""<?xml version="1.0"?>
    <xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
      <xs:element name="root">
        <xs:complexType>
          <xs:sequence>
            <xs:element name="test" type="xs:integer"/>
          </xs:sequence>
        </xs:complexType>
      </xs:element>
    </xs:schema>
    """
    schema_path = tmp_path / schema_file
    schema_path.write_bytes(schema)

    # Valid XML.
    xml = b"""<root><test>1</test></root>"""
    validate_xml(parse_xml(xml), tmp_path, schema_file)

    # Invalid XML element and value.

    xml = b"""<root><test>Hello</test><invalid/></root>"""
    with pytest.raises(ValueError) as exc:
        validate_xml(parse_xml(xml), tmp_path, schema_file, xml_path="test.xml")

    assert str(exc.value) == (
        "XML Schema validation error in test.xml:\n"
        "Line 1: /root/test: SCHEMAV_CVC_DATATYPE_VALID_1_2_1\n"
        "Line 1: /root/invalid: SCHEMAV_ELEMENT_CONTENT"
    )


@pytest.mark.parametrize(
    "xml",
    [
        # An internal entity.
        '<!DOCTYPE root [<!ENTITY x "Jane">]><root>&x;</root>',
        # An external entity.
        '<!DOCTYPE root [<!ENTITY x SYSTEM "file://{dtd}">]><root>&x;</root>',
        # An external parameter entity, which lxml's defaults once loaded.
        '<!DOCTYPE root [<!ENTITY % x SYSTEM "file://{dtd}"> %x;]><root/>',
        # An external DTD.
        '<!DOCTYPE root SYSTEM "file://{dtd}"><root/>',
    ],
)
def test_parse_xml_rejects_doctype(tmp_path: Path, xml: str):
    # The parser fails on this invalid DTD if it reads the file,
    dtd = tmp_path / "test.dtd"
    dtd.write_text("<!ENTITY y 'z'> not a DTD")

    with pytest.raises(ValueError, match="DOCTYPE"):
        parse_xml(xml.format(dtd=dtd))


def test_parse_xml_predefined_entities():
    tree = parse_xml(b"<root>a &amp; b &#233; &lt;</root>")
    assert tree.getroot().text == "a & b é <"


def test_get_xml_value():
    xml = etree.fromstring("""
    <root>
        <child attr="value">text</child>
        <empty_child/>
    </root>
    """)

    # Get element text
    value = get_xml_value("/root/child/text()", xml)
    assert value == "text"

    # Get attribute value
    value = get_xml_value("/root/child/@attr", xml)
    assert value == "value"

    # Missing node with optional=True should return None
    value = get_xml_value("/root/missing", xml, optional=True)
    assert value is None

    # Missing node with optional=False should raise
    with pytest.raises(ValueError) as exc:
        get_xml_value("/root/missing", xml)
    assert "not found" in str(exc.value)

    # Empty element text with optional=True returns None
    value = get_xml_value("/root/empty_child/text()", xml, optional=True)
    assert value is None

    # Empty element text with optional=False raises
    with pytest.raises(ValueError):
        get_xml_value("/root/empty_child/text()", xml)


def test_get_xml_values():
    xml = etree.fromstring("""
    <root>
        <child attr="x">one</child>
        <child attr="y">two</child>
        <child attr="z">three</child>
        <empty_child/>
    </root>
    """)

    # Get element text values
    values = get_xml_values("/root/child/text()", xml)
    assert values == ["one", "two", "three"]

    # Get attribute values
    values = get_xml_values("/root/child/@attr", xml)
    assert values == ["x", "y", "z"]

    # Empty element returns empty list
    values = get_xml_values("/root/empty_child/text()", xml, optional=True)
    assert values == []

    # Missing element with optional=True returns empty list
    values = get_xml_values("/root/missing", xml, optional=True)
    assert values == []

    # Missing element with optional=False raises ValueError
    with pytest.raises(ValueError) as exc:
        get_xml_values("/root/missing", xml)
    assert "not found" in str(exc.value)

    # Mix of text and empty elements
    values = get_xml_values("/root/*/text()", xml, optional=True)
    assert values == ["one", "two", "three"]
