-- Initialize pgvector and dedicated NOBYPASSRLS application role for strict RLS enforcement
CREATE EXTENSION IF NOT EXISTS vector;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'apex_app') THEN
        CREATE ROLE apex_app LOGIN PASSWORD 'apex_app_dev_pass' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
END
$$;

GRANT CONNECT ON DATABASE apex_one TO apex_app;
GRANT USAGE, CREATE ON SCHEMA public TO apex_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO apex_app;
