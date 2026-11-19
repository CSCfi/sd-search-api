-- Create the tables. Run as the admin user before grant.sql.

BEGIN;

-- Preferred terms for concepts observed while indexing a field.
CREATE TABLE public.terms_cache (
    ontology_id    TEXT        NOT NULL,
    concept_id     TEXT        NOT NULL,
    field_id       TEXT        NOT NULL,
    preferred_term TEXT        NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ontology_id, concept_id, field_id)
);

CREATE INDEX idx_terms_cache_ontology_id_updated_at
    ON public.terms_cache (ontology_id, updated_at);

-- Full ontology cache. Suitable for small ontologies (e.g. SEND).
CREATE TABLE public.ontology_cache (
    ontology_id TEXT        NOT NULL PRIMARY KEY,
    version     TEXT        NOT NULL,
    sha256      TEXT        NOT NULL,
    data        JSONB       NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Documents to be synced to OpenSearch.
CREATE TABLE public.document (
    id          TEXT        NOT NULL PRIMARY KEY,
    set_id      TEXT,
    payload     JSONB       NOT NULL,
    modified_at TIMESTAMPTZ NOT NULL,
    synced_at   TIMESTAMPTZ
);

CREATE INDEX idx_document_set_id ON public.document (set_id);
CREATE INDEX idx_document_synced_at ON public.document (synced_at);

-- Problems found while loading a document.
CREATE TABLE public.document_log (
    id          BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id TEXT        NOT NULL,
    field_id    TEXT,
    severity    TEXT        NOT NULL CHECK (severity IN ('WARNING', 'ERROR')),
    message     TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_document_log_document_id ON public.document_log (document_id);

-- Marker indicates the position of the previous incremental load.
CREATE TABLE public.load (
    id         INT         NOT NULL PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    marker     TEXT        NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE public.load_history (
    id         BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    marker     TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
