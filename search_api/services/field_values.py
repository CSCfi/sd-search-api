from collections.abc import Mapping

from search_api.api.beacon.models import BeaconFilteringTerm
from search_api.api.beacon.services import BeaconService
from search_api.api.models import FieldValue
from search_api.services.ontology.service import get_ontology_service
from search_api.services.ontology.term_cache import OntologyTermCache


async def get_field_suggestions(
    filtering_term: BeaconFilteringTerm,
    term: str,
    scope: str | None,
    beacon_service: BeaconService,
    ontology_term_services: Mapping[str, OntologyTermCache],
    ontology_id_by_field: Mapping[str, str],
    substring_match: bool = False,
    include_all_controlled_values: bool = False,
    include_other_ontology_values: bool = True,
) -> list[FieldValue]:
    """Return value suggestions for a given field and search term."""

    field_id = filtering_term.id

    def _matches(value: str) -> bool:
        value_lower = value.lower()
        term_lower = term.lower()
        if substring_match:
            return term_lower in value_lower
        return any(word.startswith(term_lower) for word in value_lower.split())

    if filtering_term.type in ("controlledValue", "keyword"):
        field_counts = await beacon_service.get_value_counts(
            field_id,
            scope,
        )
        counts = field_counts.counts
        if filtering_term.type == "controlledValue" and include_all_controlled_values:
            candidates = filtering_term.controlledValues or []
        else:
            candidates = list(counts.keys())
        return [
            FieldValue(value=v, count=counts.get(v, 0))
            for v in sorted(v for v in candidates if _matches(v))
        ]

    field_counts = await beacon_service.get_value_counts(
        field_id,
        scope,
    )
    counts = field_counts.counts
    ontology_id = ontology_id_by_field[field_id]
    term_service = ontology_term_services[ontology_id]
    preferred_terms = await term_service.get_terms_by_concept_id(
        field_id, set(counts.keys())
    )
    # The ontology decides if the term could start a concept id.
    # Concept ids are case sensitive and only matched from their start.
    is_concept_id_prefix = get_ontology_service(ontology_id).is_concept_id_prefix(term)

    def _matches_concept_id(concept_id: str) -> bool:
        return is_concept_id_prefix and concept_id.startswith(term)

    results = [
        FieldValue(
            value=preferred_term, concept_id=concept_id, count=counts[concept_id]
        )
        for preferred_term, concept_id in sorted(
            (preferred_term, concept_id)
            for concept_id, preferred_term in preferred_terms.items()
            if _matches(preferred_term) or _matches_concept_id(concept_id)
        )
    ]

    if filtering_term.type == "ontology":
        return results

    if filtering_term.type == "ontologyOrValue" and include_other_ontology_values:
        existing = {s.value for s in results}
        for text_value, count in field_counts.other_counts.items():
            if _matches(text_value) and text_value not in existing:
                results.append(FieldValue(value=text_value, count=count))

    return results


async def get_field_values(
    filtering_term: BeaconFilteringTerm,
    scope: str | None,
    beacon_service: BeaconService,
    ontology_term_services: Mapping[str, OntologyTermCache],
    ontology_id_by_field: Mapping[str, str],
    include_all_controlled_values: bool = False,
    include_other_ontology_values: bool = True,
) -> list[FieldValue]:
    """Return the values for a given field, ordered by count."""

    field_id = filtering_term.id

    field_counts = await beacon_service.get_value_counts(
        field_id,
        scope,
    )
    counts = field_counts.counts

    if filtering_term.type in ("controlledValue", "keyword"):
        if filtering_term.type == "controlledValue" and include_all_controlled_values:
            all_values = filtering_term.controlledValues or []
            sorted_values = sorted(
                ((v, counts.get(v, 0)) for v in all_values),
                key=lambda x: x[1],
                reverse=True,
            )
        else:
            sorted_values = sorted(counts.items(), key=lambda x: x[1], reverse=True)
        return [FieldValue(value=v, count=c) for v, c in sorted_values]

    term_service = ontology_term_services[ontology_id_by_field[field_id]]
    preferred_terms = await term_service.get_terms_by_concept_id(
        field_id, set(counts.keys())
    )
    results: list[tuple[str, int, str | None]] = [
        (preferred_term, counts[concept_id], concept_id)
        for concept_id, preferred_term in preferred_terms.items()
    ]

    if filtering_term.type == "ontologyOrValue" and include_other_ontology_values:
        results += [
            (text_value, count, None)
            for text_value, count in field_counts.other_counts.items()
        ]

    sorted_results = sorted(results, key=lambda x: x[1], reverse=True)
    return [
        FieldValue(value=label, count=count, concept_id=concept_id)
        for label, count, concept_id in sorted_results
    ]
