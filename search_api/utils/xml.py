"""XML functions."""

import os
from pathlib import Path
from typing import IO, cast

from lxml import etree
from lxml.etree import _Element as Element
from lxml.etree import _ElementTree as ElementTree

_xml_schema_cache: dict[str, etree.XMLSchema] = {}


def parse_xml(xml: str | bytes | Path | IO[bytes]) -> ElementTree:
    """
    Parse XML string into an element tree with normalized whitespace.

    The XML is untrusted. A DOCTYPE can declare entities that read files, reach the
    network or expand into gigabytes. The parser loads no DTD and resolves no declared
    entity, and as a second safeguard XML with a DOCTYPE is rejected. Bigpicture XML
    has none. Predefined entities such as &amp; and character references still work.

    :param xml: XML contents or file to parse.
    :raises ValueError: if the XML has a DOCTYPE.
    :return: XML element tree.
    """

    # resolve_entities is turned off, so no declared entity is expanded. The three
    # after it are lxml's defaults. They are set here so a change of defaults cannot
    # open the parser to external entities or huge documents.
    parser = etree.XMLParser(
        remove_blank_text=True,
        remove_comments=True,
        resolve_entities=False,
        load_dtd=False,
        no_network=True,
        huge_tree=False,
    )

    if isinstance(xml, Path):
        tree = etree.parse(str(xml), parser)
    elif hasattr(xml, "read"):  # IO[bytes]
        tree = etree.parse(xml, parser)
    else:
        if isinstance(xml, str):
            xml = xml.encode("utf-8")
        tree = etree.ElementTree(etree.fromstring(xml, parser))

    if tree.docinfo.doctype:
        raise ValueError("XML with a DOCTYPE is not supported.")
    return tree


def validate_xml(
    xml: ElementTree | Element,
    schema_dir: Path,
    schema_file: str,
    *,
    xml_path: str | None = None,
) -> None:
    """
    Validate XML against XML Schema.

    :param xml: XML element or element tree.
    :param schema_dir: The directory for the XML schema files.
    :param schema_file: The schema file name.
    :param xml_path: The path the XML was read from, if any, named in the error.
    :raises ValueError: if the XML is not valid.
    """
    xml_schema_path = os.path.join(schema_dir, schema_file)

    # Cache XML schemas.
    if xml_schema_path not in _xml_schema_cache:
        _xml_schema_cache[xml_schema_path] = etree.XMLSchema(
            etree.parse(xml_schema_path)
        )
    xml_schema = _xml_schema_cache[xml_schema_path]

    if not xml_schema.validate(
        xml if isinstance(xml, ElementTree) else etree.ElementTree(xml)
    ):
        # libxml2's message often quotes the value that failed. The XML is sensitive
        # metadata and should not be reported or logged. To avoid this we only report
        # the libxml2 error path and type.
        messages: list[str] = [
            f"Line {err.line}: {err.path}: {err.type_name}"
            for err in xml_schema.error_log
        ]
        source = f" in {xml_path}" if xml_path else ""
        raise ValueError(
            f"XML Schema validation error{source}:\n" + "\n".join(messages)
        )


def get_xml_value(
    path: str,
    xml: ElementTree,
    *,
    optional: bool = False,
    field_name: str | None = None,
) -> str | None:
    """
    Retrieve the value of an XML element or attribute using an XPath expression.

    The XPath expression must be absolute when the xml is an element tree and must start with /.

    :param path: XPath expression to locate the element or attribute.
    :param xml: XML element tree.
    :param optional: If True, return None instead of raising if the node or value is missing.
    :param field_name: If provided, used in error messages instead of the XPath expression.
    :return: The text or attribute value as a string, or None if optional and not found.
    """
    result = xml.xpath(path)

    field_name = field_name or f"XPath '{path}'"

    if not result:
        if optional:
            return None
        raise ValueError(f"{field_name} not found.")

    value = result[0]
    if value is None:
        if optional:
            return None
        raise ValueError(f"{field_name} not found.")

    if isinstance(value, str):
        return value

    if isinstance(value, Element):
        if not value.text:
            if optional:
                return None
            raise ValueError(f"{field_name} not found.")

        return cast(str, value.text)

    raise TypeError(f"{field_name} unexpected type: {type(value).__name__}")


def get_xml_values(
    path: str,
    xml: ElementTree | Element,
    *,
    optional: bool = False,
    field_name: str | None = None,
) -> list[str]:
    """
    Retrieve all values of an XML element or attribute using an XPath expression.

    :param path: XPath expression to locate the element(s) or attribute(s).
    :param xml: XML element tree or element.
    :param optional: If True, return empty list instead of raising if no node or value is found.
    :param field_name: If provided, used in error messages instead of the XPath expression.
    :return: List of text/attribute values. Returns empty list if optional=True and no matches found.
    """
    results = xml.xpath(path)

    field_name = field_name or f"XPath '{path}'"

    if not results:
        if optional:
            return []
        raise ValueError(f"{field_name} not found.")

    values: list[str] = []

    for value in results:
        if value is None:
            continue
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, Element):
            if value.text:
                values.append(cast(str, value.text))
        else:
            raise TypeError(f"{field_name} unexpected type: {type(value).__name__}")

    if not values and not optional:
        raise ValueError(f"{field_name} not found.")

    return values
