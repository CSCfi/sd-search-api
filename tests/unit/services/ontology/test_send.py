import pytest

from search_api.exceptions import SystemException
from search_api.services.ontology.send import (
    SendOntologySource,
    parse_send_ontology,
    parse_send_ontology_version,
)

_TAB_FILE_HEADER = (
    "Code\tCodelist Code\tCodelist Extensible (Yes/No)\tCodelist Name\t"
    "CDISC Submission Value\tCDISC Synonym(s)\tCDISC Definition\tNCI Preferred Term"
)


def test_parse_send_ontology():
    content = (
        "\n".join(
            [
                _TAB_FILE_HEADER,
                # Code list 1.
                "C158118\t\tYes\tAge Estimation Method Response\tAGESMETH\t"
                "Age Estimation Method Response\tTerminology about age estimation.\t"
                "CDISC SEND Age Estimation Method Response Terminology",
                # Code 1.
                "C158324\tC158118\t\tAge Estimation Method Response\tANIMAL RECORDS\t\t"
                "From animal records.\tAnimal Record Information",
                # Code 2.
                "C158325\tC158118\t\tAge Estimation Method Response\tESTIMATED\tApproximated\t"
                "Estimated.\tEstimated",
                # Code list 2.
                "C200000\t\tYes\tOther Category\tOTHERCAT\tOther Category\t"
                "Some other category.\tCDISC SEND Other Category Terminology",
                # Code 2.
                "C158325\tC200000\t\tOther Category\tESTIMATED-ALT\tRoughly\tEstimated.\tEstimated",
            ]
        )
        + "\n"
    ).encode()

    concepts = parse_send_ontology(content)
    by_id = {c.concept_id: c for c in concepts}
    assert set(by_id) == {"C158118", "C158324", "C158325", "C200000"}

    code_list_1 = by_id["C158118"]
    assert code_list_1.preferred_term == "Age Estimation Method Response"
    assert code_list_1.synonyms == frozenset(
        {"AGESMETH", "CDISC SEND Age Estimation Method Response Terminology"}
    )
    assert code_list_1.parent_ids == frozenset()

    code_1 = by_id["C158324"]
    assert code_1.preferred_term == "Animal Record Information"
    assert code_1.synonyms == frozenset({"ANIMAL RECORDS"})
    assert code_1.parent_ids == frozenset({"C158118"})

    code_2 = by_id["C158325"]
    assert code_2.preferred_term == "Estimated"
    assert code_2.synonyms == frozenset(
        {"ESTIMATED", "Approximated", "ESTIMATED-ALT", "Roughly"}
    )
    assert code_2.parent_ids == frozenset({"C158118", "C200000"})

    code_list_2 = by_id["C200000"]
    assert code_list_2.preferred_term == "Other Category"
    assert code_list_2.synonyms == frozenset(
        {"OTHERCAT", "CDISC SEND Other Category Terminology"}
    )
    assert code_list_2.parent_ids == frozenset()


def test_parse_send_ontology_missing_code_raises():
    content = (
        "\n".join(
            [
                _TAB_FILE_HEADER,
                "\tC158118\t\tAge Estimation Method Response\tNO CODE\t\t"
                "Missing Code.\tMissing Code",
            ]
        )
        + "\n"
    ).encode()
    with pytest.raises(SystemException):
        parse_send_ontology(content)


def test_parse_send_ontology_missing_preferred_term_raises():
    content = (
        "\n".join(
            [
                _TAB_FILE_HEADER,
                "C999999\tC158118\t\tAge Estimation Method Response\t"
                "NO PREFERRED TERM\t\tMissing preferred term.\t",
            ]
        )
        + "\n"
    ).encode()
    with pytest.raises(SystemException):
        parse_send_ontology(content)


def test_parse_send_ontology_version_uses_modified_date():
    content = (
        b"Quarter\t\tRelease Date\t\tModified date\t\tReason\r\r\n"
        b"\r\r\n"
        b"Q1 2026\t\t2026-03-27\t\t2026-03-30\t\t"
        b"CDISC definitions added to concepts C48918 and C53322."
    )

    assert parse_send_ontology_version(content) == "2026-03-30"


def test_parse_send_ontology_version_uses_release_date():
    content = (
        b"Quarter\t\tRelease Date\t\tModified date\t\tReason\r\r\n"
        b"\r\r\n"
        b"Q2 2026\t\t2026-06-15\t\tNo modification yet."
    )
    assert parse_send_ontology_version(content) == "2026-06-15"


def test_parse_send_ontology_version_uses_max_date():
    content = (
        b"Quarter\t\tRelease Date\t\tModified date\t\tReason\r\r\n"
        b"\r\r\n"
        b"Q1 2026\t\t2026-03-27\t\t2026-03-30\t\tFirst.\r\r\n"
        b"Q2 2026\t\t2026-06-15\t\tNo modification yet."
    )
    assert parse_send_ontology_version(content) == "2026-06-15"


def test_parse_send_ontology_version_no_date_raises():
    content = b"Quarter\t\tRelease Date\t\tModified date\t\tReason\r\r\n"
    with pytest.raises(SystemException):
        parse_send_ontology_version(content)


@pytest.mark.parametrize(
    "version,other,expected",
    [
        ("2026-03-30", "2026-03-27", True),
        ("2026-03-27", "2026-03-30", False),
        ("2026-03-30", "2026-03-30", False),
        # A date is compared as a date, not as a string.
        ("2026-09-01", "2026-10-01", False),
    ],
)
def test_is_newer_compares_release_dates(version, other, expected):
    assert SendOntologySource().is_newer(version, other) is expected
