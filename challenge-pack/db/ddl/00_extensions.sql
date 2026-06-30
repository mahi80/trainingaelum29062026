-- 00_extensions.sql — extensions + schemas for the auto-loan warehouse
CREATE EXTENSION IF NOT EXISTS vector;        -- pgvector (embeddings)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";   -- uuid_generate_v4()
CREATE EXTENSION IF NOT EXISTS pg_trgm;       -- fuzzy text (NL->SQL helpers)

CREATE SCHEMA IF NOT EXISTS ref;   -- reference / lookup dimensions
CREATE SCHEMA IF NOT EXISTS loan;  -- operational loan-origination data
CREATE SCHEMA IF NOT EXISTS doc;   -- documents / pages / extractions
CREATE SCHEMA IF NOT EXISTS app;   -- application: auth, sessions, embeddings
