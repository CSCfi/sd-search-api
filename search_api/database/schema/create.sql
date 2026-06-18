CREATE TABLE snomed (
    concept_id     TEXT        NOT NULL,
    field_id       TEXT        NOT NULL,
    preferred_term TEXT        NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (concept_id, field_id)
);

CREATE INDEX idx_snomed_updated_at ON snomed (updated_at);

CREATE TABLE document (
    id          TEXT        NOT NULL PRIMARY KEY,
    payload     JSONB       NOT NULL,
    modified_at TIMESTAMPTZ,
    synced_at   TIMESTAMPTZ
);

CREATE INDEX idx_document_synced_at ON document (synced_at);
