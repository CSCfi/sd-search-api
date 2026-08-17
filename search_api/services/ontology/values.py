"""Resolve valid concept ids and cache ontology terms."""

from collections.abc import Sequence
from typing import cast

from psycopg import AsyncCursor
from pydantic import BaseModel, ConfigDict

from search_api.api.beacon.models import BeaconFilteringTerm
from search_api.api.opensearch.models import ExtractedDocument, OpenSearchFieldValue
from search_api.database.document_log import write_document_log
from search_api.database.models import StoredDocumentLog
from search_api.exceptions import SystemException
from search_api.services.ontology.service import (
    OntologyService,
    get_ontology_id_by_field,
    get_ontology_service,
)
from search_api.services.ontology.term_cache import OntologyTermCache


class OntologyBinding(BaseModel):
    """Information to resolve an ontology field value to a valid concept id."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    term: BeaconFilteringTerm
    ontology_id: str
    ontology: OntologyService
    term_cache: OntologyTermCache


class OntologyValueWithBinding(BaseModel):
    """Ontology field value with information to resolve it to a valid concept id."""

    model_config = ConfigDict(frozen=True)

    value: OpenSearchFieldValue
    binding: OntologyBinding

    @property
    def field_id(self) -> str:
        return self.value.field.id

    @property
    def concept_id(self) -> str | None:
        return self._concept_id_and_meaning[0]

    @property
    def meaning(self) -> str | None:
        return self._concept_id_and_meaning[1]

    @property
    def _concept_id_and_meaning(self) -> tuple[str | None, str | None]:
        if not isinstance(self.value.value, tuple):
            raise SystemException(
                f"Invalid {self.value.value!r} for an ontology field '{self.field_id}'."
            )
        return cast(tuple[str | None, str | None], self.value.value)


def get_ontology_bindings(
    filtering_terms: Sequence[BeaconFilteringTerm],
    term_caches: dict[str, OntologyTermCache],
) -> dict[str, OntologyBinding]:
    terms = {term.id: term for term in filtering_terms}
    return {
        field_id: OntologyBinding(
            term=terms[field_id],
            ontology_id=ontology_id,
            ontology=get_ontology_service(ontology_id),
            term_cache=term_caches[ontology_id],
        )
        for field_id, ontology_id in get_ontology_id_by_field(filtering_terms).items()
    }


async def _resolve_concept_id_by_meaning(
    cur: AsyncCursor, document_id: str, ontology_value: OntologyValueWithBinding
) -> str | None:
    """Resolve valid concept id from a textual concept value."""
    binding = ontology_value.binding
    meaning = (ontology_value.meaning or "").strip()
    resolved = (
        await binding.ontology.resolve_concept_ids(
            meaning, binding.term, binding.term_cache
        )
        if meaning
        else set()
    )

    if len(resolved) == 1:
        (concept_id,) = resolved
        await write_document_log(
            cur,
            StoredDocumentLog(
                document_id=document_id,
                field_id=ontology_value.field_id,
                severity="WARNING",
                message=f"Concept id '{concept_id}' was resolved from the provided "
                f"textual concept value '{meaning}' for "
                f"field '{ontology_value.field_id}'.",
            ),
        )
        return concept_id

    if resolved:
        await write_document_log(
            cur,
            StoredDocumentLog(
                document_id=document_id,
                field_id=ontology_value.field_id,
                severity="WARNING",
                message=f"Textual concept value '{meaning}' resolves to several "
                f"concept ids for field '{ontology_value.field_id}': "
                f"{', '.join(sorted(resolved))}.",
            ),
        )
    return None


async def _resolve_retired_concept_id(
    cur: AsyncCursor,
    document_id: str,
    ontology_value: OntologyValueWithBinding,
    concept_id: str,
) -> str:
    """Resolve valid concept id for a retired concept id.

    Returns the provided concept id if not found.
    """
    binding = ontology_value.binding
    replacement_id = await binding.ontology.replacement_concept_id(concept_id)
    if replacement_id is None:
        return concept_id
    await write_document_log(
        cur,
        StoredDocumentLog(
            document_id=document_id,
            field_id=ontology_value.field_id,
            severity="WARNING",
            message=f"Provided concept id '{concept_id}' was replaced by "
            f"'{replacement_id}' for field '{ontology_value.field_id}'.",
        ),
    )

    return replacement_id


async def _resolve_concept_id(
    cur: AsyncCursor,
    document_id: str,
    ontology_value: OntologyValueWithBinding,
    replace_concepts: bool,
) -> OpenSearchFieldValue | None:
    """Resolve a valid concept id and assign it to a copy of the OpenSearch field value."""
    binding = ontology_value.binding
    provided_concept_id = ontology_value.concept_id

    # Check if the provided concept id resolves to a valid concept id.
    concept_id = (
        provided_concept_id
        if provided_concept_id and binding.ontology.is_concept_id(provided_concept_id)
        else None
    )
    if provided_concept_id is not None and concept_id is None:
        await write_document_log(
            cur,
            StoredDocumentLog(
                document_id=document_id,
                field_id=ontology_value.field_id,
                severity="WARNING",
                message=f"The provided concept id '{provided_concept_id}' is invalid for "
                f"field '{ontology_value.field_id}' of "
                f"ontology '{binding.ontology_id}'.",
            ),
        )

    if concept_id is None:
        # Check if the provided textual representation resolves to a valid concept id.
        concept_id = await _resolve_concept_id_by_meaning(
            cur, document_id, ontology_value
        )
    if concept_id is not None and replace_concepts:
        # Check if the resolved concept id is retired and resolves to a valid concept id.
        concept_id = await _resolve_retired_concept_id(
            cur, document_id, ontology_value, concept_id
        )

    if concept_id is None:
        # A valid concept id could not be resolved.
        await write_document_log(
            cur,
            StoredDocumentLog(
                document_id=document_id,
                field_id=ontology_value.field_id,
                severity="ERROR",
                message=f"Concept id could not be resolved for ontology field "
                f"'{ontology_value.field_id}'.",
            ),
        )
        return None
    return ontology_value.value.model_copy(update={"resolved_concept_id": concept_id})


async def _resolve_concept_ids(
    cur: AsyncCursor,
    document_id: str,
    values: list[OpenSearchFieldValue],
    bindings: dict[str, OntologyBinding],
    replace_concepts: bool,
) -> list[OpenSearchFieldValue]:
    """Resolve valid concept ids and assign them to copies of the OpenSearchFieldValues."""
    resolved: list[OpenSearchFieldValue] = []
    for value in values:
        binding = bindings.get(value.field.id)
        if binding is None:
            # Non-ontology field.
            resolved.append(value)
            continue
        value_with_resolved_id = await _resolve_concept_id(
            cur,
            document_id,
            OntologyValueWithBinding(value=value, binding=binding),
            replace_concepts,
        )
        # If an concept in 'ontology' or 'ontologyOrValue' can't be resolved
        # to a valid concept id, the value is dropped.
        if value_with_resolved_id is not None:
            resolved.append(value_with_resolved_id)
    return resolved


async def resolve_concepts(
    cur: AsyncCursor,
    doc: ExtractedDocument,
    bindings: dict[str, OntologyBinding],
    replace_concepts: bool = True,
) -> None:
    """Resolve concept ids in the document.

    Assigns resolved concept ids to copies of OpenSearchFieldValues.
    """

    # Resolve concept ids in top level fields and assign them to copies of OpenSearchFieldValues.
    doc.values = await _resolve_concept_ids(
        cur, doc.id, doc.values, bindings, replace_concepts
    )
    # Resolve concept ids in nested groups and assign them to copies of OpenSearchFieldValues.
    for group in doc.groups:
        group.values = await _resolve_concept_ids(
            cur, doc.id, group.values, bindings, replace_concepts
        )


async def cache_concept_terms(
    cur: AsyncCursor, doc: ExtractedDocument, bindings: dict[str, OntologyBinding]
) -> None:
    """Cache the preferred term of every concept in the document.

    Reads the ``resolved_concept_id`` of each value, so ``resolve_concepts`` must have
    run over the document first.
    """

    resolved_concept_ids_by_field: dict[str, set[str]] = {}
    for value in doc.all_values:
        if value.field.id in bindings and value.resolved_concept_id is not None:
            resolved_concept_ids_by_field.setdefault(value.field.id, set()).add(
                value.resolved_concept_id
            )

    for field_id, resolved_concept_ids in resolved_concept_ids_by_field.items():
        binding = bindings[field_id]
        unresolved_concept_ids = await binding.term_cache.cache_preferred_terms(
            field_id, resolved_concept_ids, binding.ontology
        )
        for unresolved_concept_id in sorted(unresolved_concept_ids):
            await write_document_log(
                cur,
                StoredDocumentLog(
                    document_id=doc.id,
                    field_id=field_id,
                    severity="ERROR",
                    message=f"Value '{unresolved_concept_id}' was not found in "
                    f"ontology '{binding.ontology_id}'.",
                ),
            )
