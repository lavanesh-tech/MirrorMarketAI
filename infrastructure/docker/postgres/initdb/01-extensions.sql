-- Runs once, when the local Postgres volume is first initialised.
--
-- Enables pgvector so the local database matches what later phases expect.
-- From Phase 2 onward, Alembic migrations also run
-- `CREATE EXTENSION IF NOT EXISTS vector`, which is what makes the extension
-- exist on managed databases (AWS RDS) where this script never runs.
CREATE EXTENSION IF NOT EXISTS vector;
