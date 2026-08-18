import logging
from collections.abc import Iterable

from search_api.conf import cache_config
from search_api.database.models import StoredTerm
from search_api.database.terms_cache import (
    insert_terms,
    read_concept_ids_by_field,
    read_terms,
    read_updated_at,
    update_terms,
)
from search_api.services.ontology.service import OntologyService, normalise_term
from search_api.services.poller import UpdatedPoller

logger = logging.getLogger(__name__)

type PreferredTermByFieldAndConceptIdMap = dict[str, dict[str, str]]
type ConceptIdsByFieldAndPreferredTermMap = dict[str, dict[str, set[str]]]


class OntologyTermCache:
    """Persistent cache mapping indexed concept IDs to preferred terms.

    The cache is used by ``/values`` and ``/suggestions`` endpoints.
    """

    def __init__(self, ontology_id: str, refresh_interval: float = 300.0) -> None:
        self._ontology_id = ontology_id
        self._preferred_term_by_id: PreferredTermByFieldAndConceptIdMap = {}
        self._ids_by_preferred_term: ConceptIdsByFieldAndPreferredTermMap = {}
        self._poller = UpdatedPoller(
            "term cache",
            lambda: read_updated_at(ontology_id),
            lambda: self.load(),
            refresh_interval,
        )

    def _index_term(self, field_id: str, concept_id: str, preferred_term: str) -> None:
        """Map concept_id and preferred_term to each other in both directions."""
        preferred_term_by_id = self._preferred_term_by_id.setdefault(field_id, {})
        ids_by_preferred_term = self._ids_by_preferred_term.setdefault(field_id, {})
        existing_preferred_term = preferred_term_by_id.get(concept_id)
        if existing_preferred_term is not None:
            # Remove existing preferred term to concept id mapping.
            ids_by_preferred_term[normalise_term(existing_preferred_term)].discard(
                concept_id
            )

        preferred_term_by_id[concept_id] = preferred_term
        ids_by_preferred_term.setdefault(normalise_term(preferred_term), set()).add(
            concept_id
        )

    async def load(self) -> None:
        """Load all terms from the store into the in-memory cache.

        Call this once at startup before serving requests.
        """
        terms = await read_terms(self._ontology_id)
        self._preferred_term_by_id = {}
        self._ids_by_preferred_term = {}
        for term in terms:
            self._index_term(term.field_id, term.concept_id, term.preferred_term)
        logger.info("Loaded %d preferred term(s) into memory cache.", len(terms))

    async def get_terms_by_concept_id(
        self, field_id: str, concept_ids: set[str]
    ) -> dict[str, str]:
        preferred_term_by_id = self._preferred_term_by_id.get(field_id, {})
        return {
            concept_id: preferred_term
            for concept_id in concept_ids
            if (preferred_term := preferred_term_by_id.get(concept_id)) is not None
        }

    async def get_concept_ids_by_term(self, field_id: str, term: str) -> set[str]:
        return set(
            self._ids_by_preferred_term.get(field_id, {}).get(normalise_term(term), ())
        )

    async def cache_preferred_terms(
        self, field_id: str, concept_ids: set[str], ontology: OntologyService
    ) -> set[str]:
        """Cache the preferred terms of the concept ids not cached yet.

        :returns: the concept ids the ontology did not resolve, so nothing could be
            cached for them.
        """
        missing_concept_ids = concept_ids.difference(
            self._preferred_term_by_id.get(field_id, {})
        )
        if not missing_concept_ids:
            return set()

        logger.info(
            "Resolving %d new concept ID(s) from the ontology.",
            len(missing_concept_ids),
        )
        terms_by_concept_id = await ontology.get_preferred_terms(missing_concept_ids)
        unresolved = missing_concept_ids - set(terms_by_concept_id)
        new_terms = [
            StoredTerm(
                field_id=field_id, concept_id=concept_id, preferred_term=preferred_term
            )
            for concept_id, preferred_term in terms_by_concept_id.items()
        ]
        if not new_terms:
            return unresolved

        await insert_terms(self._ontology_id, new_terms)
        for term in new_terms:
            self._index_term(term.field_id, term.concept_id, term.preferred_term)

        logger.info(
            "Cached preferred terms for %d (concept_id, field_id) pair(s).",
            len(new_terms),
        )
        return unresolved

    async def refresh(self, ontology: OntologyService) -> None:
        """Update preferred term using the current ontology release."""
        concept_ids_by_field = await read_concept_ids_by_field(self._ontology_id)
        if not concept_ids_by_field:
            logger.info("No concept IDs stored — nothing to refresh.")
            return

        total_updated = 0
        for field_id, concept_ids in concept_ids_by_field.items():
            terms_by_concept_id = await ontology.get_preferred_terms(concept_ids)
            if not terms_by_concept_id:
                logger.warning(
                    "No preferred terms returned from the ontology for field '%s'.",
                    field_id,
                )
                continue
            preferred_term_by_id = self._preferred_term_by_id.get(field_id, {})
            renamed_terms = [
                StoredTerm(
                    field_id=field_id,
                    concept_id=concept_id,
                    preferred_term=preferred_term,
                )
                for concept_id, preferred_term in terms_by_concept_id.items()
                if preferred_term_by_id.get(concept_id) != preferred_term
            ]
            if not renamed_terms:
                continue
            await update_terms(self._ontology_id, renamed_terms)
            for term in renamed_terms:
                self._index_term(term.field_id, term.concept_id, term.preferred_term)
            total_updated += len(renamed_terms)

        logger.info("Refreshed %d preferred term(s).", total_updated)

    async def start(self) -> None:
        """Start the background task that reloads the cache."""
        await self._poller.start()

    def stop(self) -> None:
        """Stop the background task that reloads the cache."""
        self._poller.stop()


def create_term_caches(
    ontology_ids: Iterable[str],
) -> dict[str, OntologyTermCache]:
    """Create one preferred term cache per ontology id, keyed by that id.

    The cache is per ontology. Callers map a field to its
    ontology id and look the cache up here, so every field resolving against
    the same ontology shares the one cache instance for that id.
    """
    refresh_interval = cache_config().TERM_CACHE_REFRESH
    return {
        ontology_id: OntologyTermCache(ontology_id, refresh_interval)
        for ontology_id in ontology_ids
    }
