-- Grant the api user access to the tables. Run as the admin user after create.sql.
GRANT SELECT, INSERT, UPDATE, DELETE
ON TABLE
    public.terms_cache,
    public.ontology_cache,
    public.document,
    public.document_log,
    public.load,
    public.load_history
TO :"api_user";
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"api_user";
