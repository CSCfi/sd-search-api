-- Preferred terms for concepts observed while indexing a field.
CREATE TABLE terms_cache (
    ontology_id    TEXT        NOT NULL,
    concept_id     TEXT        NOT NULL,
    field_id       TEXT        NOT NULL,
    preferred_term TEXT        NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ontology_id, concept_id, field_id)
);

CREATE INDEX idx_terms_cache_updated_at ON terms_cache (updated_at);

-- Full ontology cache. Suitable for small ontologies (e.g. SEND).
CREATE TABLE ontology_cache (
    ontology_id TEXT        NOT NULL PRIMARY KEY,
    version     TEXT        NOT NULL,
    sha256      TEXT        NOT NULL,
    data        JSONB       NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE document (
    id          TEXT        NOT NULL PRIMARY KEY,
    payload     JSONB       NOT NULL,
    modified_at TIMESTAMPTZ,
    synced_at   TIMESTAMPTZ
);

CREATE INDEX idx_document_synced_at ON document (synced_at);
