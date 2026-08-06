"""XML functions."""

import os
from pathlib import Path
from typing import IO, cast

from lxml import etree
from lxml.etree import _Element as Element  # noqa
from lxml.etree import _ElementTree as ElementTree  # noqa

_xml_schema_cache: dict[str, etree.XMLSchema] = {}


def parse_xml(xml: str | bytes | Path | IO[bytes]) -> ElementTree:
    """
    Parse XML string into an element tree with normalized whitespace.

    :param xml: XML contents or file to parse.
    :return: XML element tree.
    """

    parser = etree.XMLParser(remove_blank_text=True, remove_comments=True)

    if isinstance(xml, Path):
        return etree.parse(str(xml), parser)

    if hasattr(xml, "read"):  # IO[bytes]
        return etree.parse(xml, parser)

    if isinstance(xml, str):
        xml = xml.encode("utf-8")

    if isinstance(xml, Path):
        with xml.open("rb") as f:
            etree.parse(f, parser=parser, base_url=str(xml))

    return etree.ElementTree(etree.fromstring(xml, parser))


def validate_xml(
    xml: ElementTree | Element, schema_dir: Path, schema_file: str
) -> None:
    """
    Validate XML against XML Schema. Raise SchemaValidationException on failure.

    :param xml: XML element or element tree.
    :param schema_dir: The directory for the XML schema files.
    :param schema_file: The schema file name.
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
        messages: list[str] = [
            f"Line {err.line}: {err.message}" for err in xml_schema.error_log
        ]
        raise ValueError("XML Schema validation error:\n" + "\n".join(messages))


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
    try:
        result = xml.xpath(path)
    except Exception as e:
        raise e

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

    raise ValueError(f"{field_name} unexpected type: {type(value).__name__}")


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
    try:
        results = xml.xpath(path)
    except Exception as e:
        raise e

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
            raise ValueError(f"{field_name} unexpected type: {type(value).__name__}")

    if not values and not optional:
        raise ValueError(f"{field_name} not found.")

    return values
