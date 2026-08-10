"""Generic Beacon V2 API router, built per deployment domain."""

import asyncio
import os
from collections.abc import Mapping, Sequence

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from search_api.database.repository import is_healthy as is_database_healthy
from search_api.ai.models import AISearchResult
from search_api.ai.services import AIService
from search_api.api.beacon.models import (
    BeaconBooleanResponse,
    BeaconCountResponse,
    BeaconFilteringGroup,
    BeaconFilteringQualifier,
    BeaconFilteringTerm,
    BeaconFilteringScope,
    BeaconFilteringTerms,
    BeaconFilteringTermsResponse,
    BeaconInfo,
    BeaconInfoMeta,
    BeaconInfoResponse,
    BeaconQueryRequest,
    BeaconResponseMeta,
    BeaconResultCountResponseSummary,
    BeaconResultExistsResponseSummary,
    BeaconSchema,
)
from search_api.api.beacon.services import BeaconService
from search_api.api.domain import Domain
from search_api.api.models import AIQueryRequest, FieldValue
from search_api.api.qualifiers import (
    QUALIFIER_VALUE_SEPARATOR,
    validate_requested_qualifiers,
)
from search_api.conf import feature_config
from search_api.exceptions import SystemException, UserException
from search_api.services.ontology.service import get_ontology_service
from search_api.services.ontology.term_cache import OntologyTermCache


# Dependency providers are module-level so tests can override them by identity
# via app.dependency_overrides. They resolve the active domain and shared
# services from app.state, which the lifespan populates.
def get_beacon_service(request: Request) -> BeaconService:
    domain: Domain = request.app.state.domain
    return domain.beacon_service_factory(request.app.state.search)


def get_ai_service(request: Request) -> AIService:
    domain: Domain = request.app.state.domain
    return AIService(
        domain.filtering_terms,
        domain.ai_assistant_description,
        domain.ai_result_model,
        domain.ai_result_instructions,
    )


def get_ontology_term_services(
    request: Request,
) -> dict[str, OntologyTermCache]:
    return request.app.state.ontology_term_services


def make_beacon_router(domain: Domain) -> APIRouter:
    """Build the Beacon V2 router for a deployment domain."""
    router = APIRouter()
    result_sets_response_model = domain.result_sets_response_model
    ontology_id_by_field = domain.ontology_id_by_field
    valid_scopes = {scope.id for scope in domain.filtering_scopes}

    def validate_scope(scope: str | None) -> str | None:
        """Reject a scope the deployment does not declare."""
        if scope is not None and scope not in valid_scopes:
            raise UserException(
                f"Unsupported scope: {scope!r}. Valid scopes: {sorted(valid_scopes)}."
            )
        return scope

    def validate_field_scope(
        term: BeaconFilteringTerm, scope: str | None
    ) -> str | None:
        """Reject a scope the field does not belong to.

        A field is only indexed for documents in its own scopes, so counting its
        values in another scope would always return nothing.
        """
        validate_scope(scope)
        if scope is not None and scope not in term.scopes:
            raise UserException(
                f"Field '{term.id}' is not in scope {scope!r}. "
                f"Field scopes: {sorted(term.scopes)}."
            )
        return scope

    def validate_qualifiers(qualifiers: Mapping[str, Sequence[str]]) -> None:
        validate_requested_qualifiers(qualifiers, domain.filtering_qualifiers)

    def parse_qualifiers(params: Sequence[str]) -> dict[str, list[str]]:
        """Parse <qualifier id>:<value> params."""
        parsed: dict[str, list[str]] = {}
        for item in params:
            qualifier_id, separator, value = item.partition(QUALIFIER_VALUE_SEPARATOR)
            if not separator or not value:
                raise UserException(
                    f"Invalid qualifier {item!r}; expected "
                    f"'<qualifier id>{QUALIFIER_VALUE_SEPARATOR}<value>'."
                )
            parsed.setdefault(qualifier_id, []).append(value)
        validate_qualifiers(parsed)
        return parsed

    # The info and filtering_terms responses share the same beacon meta.
    meta = BeaconInfoMeta(
        beaconId=domain.beacon_id,
        returnedSchemas=[BeaconSchema(entityType=s) for s in domain.schemas],
    )
    info_response = BeaconInfoResponse(
        meta=meta,
        response=BeaconInfo(
            id=domain.beacon_id,
            name=domain.beacon_name,
            environment=os.getenv("DEPLOYMENT_ENV", "dev"),
        ),
    )
    filtering_terms_response = BeaconFilteringTermsResponse(
        meta=meta,
        response=BeaconFilteringTerms(filteringTerms=domain.filtering_terms),
    )

    @router.get(
        "/info",
        response_model=BeaconInfoResponse,
        response_model_exclude_none=True,
    )
    async def info() -> BeaconInfoResponse:
        return info_response

    @router.get(
        "/filtering_terms",
        response_model=BeaconFilteringTermsResponse,
        response_model_exclude_none=True,
    )
    async def filtering_terms() -> BeaconFilteringTermsResponse:
        return filtering_terms_response

    @router.get("/filtering_groups", response_model=list[BeaconFilteringGroup])
    async def filtering_groups() -> list[BeaconFilteringGroup]:
        return list(domain.filtering_groups)

    @router.get("/filtering_scopes", response_model=list[BeaconFilteringScope])
    async def filtering_scopes() -> list[BeaconFilteringScope]:
        return list(domain.filtering_scopes)

    @router.get("/filtering_qualifiers", response_model=list[BeaconFilteringQualifier])
    async def filtering_qualifiers() -> list[BeaconFilteringQualifier]:
        return list(domain.filtering_qualifiers)

    @router.post(
        "/query",
        response_model=(
            BeaconBooleanResponse | BeaconCountResponse | result_sets_response_model
        ),
        response_model_exclude_none=True,
    )
    async def query(
        request: BeaconQueryRequest,
        beacon_service: BeaconService = Depends(get_beacon_service),
        ontology_term_services: dict[str, OntologyTermCache] = Depends(
            get_ontology_term_services
        ),
    ):
        validate_scope(request.query.requestedScope)
        validate_qualifiers(request.query.requestedQualifiers)

        ontology_filters = [
            f for f in request.query.filters if f.id in ontology_id_by_field
        ]
        other_filters = [
            f for f in request.query.filters if f.id not in ontology_id_by_field
        ]

        # Resolve ontology filter values to concept IDs, and optionally expand to
        # descendants. The provider is selected per term by its ``ontology.id``.
        try:
            resolved_ontology_filters = list(
                await asyncio.gather(
                    *[
                        get_ontology_service(
                            ontology_id_by_field[f.id]
                        ).prepare_ontology_filter(
                            f,
                            domain.filtering_terms,
                            ontology_term_services.get(ontology_id_by_field[f.id]),
                        )
                        for f in ontology_filters
                    ]
                )
            )
        except Exception as e:
            raise SystemException("Ontology service error.") from e

        granularity = request.query.requestedGranularity
        filters = other_filters + resolved_ontology_filters
        response = await beacon_service.query(
            filters=filters,
            granularity=granularity,
            scope=request.query.requestedScope,
            qualifiers=request.query.requestedQualifiers,
        )
        num_results = len(response.resultSet)
        exists = num_results > 0
        meta = BeaconResponseMeta(
            returnedGranularity=granularity, beaconId=domain.beacon_id
        )

        if granularity == "boolean":
            return BeaconBooleanResponse(
                meta=meta,
                responseSummary=BeaconResultExistsResponseSummary(exists=exists),
            )

        if granularity == "count":
            return BeaconCountResponse(
                meta=meta,
                responseSummary=BeaconResultCountResponseSummary(
                    exists=exists,
                    numTotalResults=num_results,
                ),
            )

        if granularity == "record":
            return result_sets_response_model(
                meta=meta,
                responseSummary=BeaconResultCountResponseSummary(
                    exists=exists, numTotalResults=num_results
                ),
                response=response,
            )

        raise UserException(f"Unsupported granularity: {granularity!r}")

    if feature_config().FEATURE_AI:

        @router.post(
            "/ai/query",
            response_model=domain.ai_result_model,
        )
        async def ai_query(
            request: AIQueryRequest,
            beacon_service: BeaconService = Depends(get_beacon_service),
            ai_service: AIService = Depends(get_ai_service),
        ) -> AISearchResult:
            return await ai_service.search(request.query, beacon_service)

    @router.get(
        "/filtering_terms/{field_id}/suggestions",
        response_model=list[FieldValue],
    )
    async def suggestions(
        field_id: str = Path(description="Filtering term field ID."),
        term: str = Query(description="Partial text to search for."),
        substring_match: bool = Query(
            default=False,
            description="Use substring matching instead of word-boundary prefix matching.",
        ),
        include_all_controlled_values: bool = Query(
            default=False,
            description="When True, include all controlled values. When False, only include indexed values.",
        ),
        include_other_ontology_values: bool = Query(
            default=True,
            description="When True, include free-text ontology field values.",
        ),
        scope: str | None = Query(
            default=None,
            description=(
                "Count only values of documents in this scope. All scopes are "
                "counted when it is not given."
            ),
        ),
        qualifier: list[str] = Query(
            default=[],
            description=(
                "Restrict a qualifier to the given values, as '<qualifier id>:<value>'. "
                "Repeat the parameter for several values. A qualifier that is not "
                "given is not filtered on, so all of its values are counted."
            ),
        ),
        beacon_service: BeaconService = Depends(get_beacon_service),
        ontology_term_services: dict[str, OntologyTermCache] = Depends(
            get_ontology_term_services
        ),
    ) -> list[FieldValue]:
        """Return value suggestions for a given field and search term."""
        filtering_term = next(
            (t for t in domain.filtering_terms if t.id == field_id), None
        )
        if filtering_term is None:
            raise UserException(f"Unknown field: '{field_id}'.")

        if filtering_term.type not in (
            "controlledValue",
            "keyword",
            "ontology",
            "ontologyOrValue",
        ):
            raise UserException(
                f"Suggestions are not supported for field '{field_id}' (type '{filtering_term.type}')."
            )

        def _matches(value: str) -> bool:
            value_lower = value.lower()
            term_lower = term.lower()
            if substring_match:
                return term_lower in value_lower
            return any(word.startswith(term_lower) for word in value_lower.split())

        if filtering_term.type in ("controlledValue", "keyword"):
            field_counts = await beacon_service.get_value_counts(
                field_id,
                validate_field_scope(filtering_term, scope),
                parse_qualifiers(qualifier),
            )
            counts = field_counts.counts
            if (
                filtering_term.type == "controlledValue"
                and include_all_controlled_values
            ):
                candidates = filtering_term.controlledValues or []
            else:
                candidates = list(counts.keys())
            return [
                FieldValue(value=v, count=counts.get(v, 0))
                for v in sorted(v for v in candidates if _matches(v))
            ]

        field_counts = await beacon_service.get_value_counts(
            field_id,
            validate_field_scope(filtering_term, scope),
            parse_qualifiers(qualifier),
        )
        counts = field_counts.counts
        term_service = ontology_term_services[ontology_id_by_field[field_id]]
        preferred_terms = await term_service.get_terms_by_concept_id(
            field_id, set(counts.keys())
        )
        results = [
            FieldValue(
                value=preferred_term, concept_id=concept_id, count=counts[concept_id]
            )
            for preferred_term, concept_id in sorted(
                (preferred_term, concept_id)
                for concept_id, preferred_term in preferred_terms.items()
                if _matches(preferred_term)
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

    @router.get(
        "/filtering_terms/{field_id}/values",
        response_model=list[FieldValue],
    )
    async def values(
        field_id: str = Path(description="Filtering term field ID."),
        include_all_controlled_values: bool = Query(
            default=False,
            description="When True, include all controlled values with count 0 for unindexed values. "
            "When False, only include indexed values.",
        ),
        include_other_ontology_values: bool = Query(
            default=True,
            description="When True, include free-text ontology field values.",
        ),
        scope: str | None = Query(
            default=None,
            description=(
                "Include values in this scope. All values included when scope is not given."
            ),
        ),
        qualifier: list[str] = Query(
            default=[],
            description=(
                "Include values labelled with this qualifier, provided as '<qualifier id>:<value>'. "
                "Repeat the parameter for several values. A qualifier that is not given is "
                "not filtered on."
            ),
        ),
        beacon_service: BeaconService = Depends(get_beacon_service),
        ontology_term_services: dict[str, OntologyTermCache] = Depends(
            get_ontology_term_services
        ),
    ) -> list[FieldValue]:
        """Return the values for a given field, ordered by count."""
        filtering_term = next(
            (t for t in domain.filtering_terms if t.id == field_id), None
        )
        if filtering_term is None:
            raise UserException(f"Unknown field: '{field_id}'.")

        if filtering_term.type not in (
            "controlledValue",
            "keyword",
            "ontology",
            "ontologyOrValue",
        ):
            raise UserException(
                f"Values are not supported for field '{field_id}' (type '{filtering_term.type}')."
            )

        field_counts = await beacon_service.get_value_counts(
            field_id,
            validate_field_scope(filtering_term, scope),
            parse_qualifiers(qualifier),
        )
        counts = field_counts.counts

        if filtering_term.type in ("controlledValue", "keyword"):
            if (
                filtering_term.type == "controlledValue"
                and include_all_controlled_values
            ):
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

    @router.get("/health")
    async def health(service: BeaconService = Depends(get_beacon_service)):
        """Report whether both database and OpenSearch are healthy."""
        try:
            search_healthy, database_healthy = await asyncio.gather(
                service.is_healthy(), is_database_healthy()
            )
        except Exception as e:
            raise SystemException("Health check failed.") from e
        if not search_healthy or not database_healthy:
            raise HTTPException(
                status_code=503,
                detail={
                    "status": "unhealthy",
                    "search": "ok" if search_healthy else "unhealthy",
                    "database": "ok" if database_healthy else "unhealthy",
                },
            )
        return {"status": "ok"}

    return router
