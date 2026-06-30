-- 80_vector.sql — pgvector tables (dim = nomic-embed-text via Ollama = 768)
-- Document/page chunk embeddings (RAG over scanned PDFs)
CREATE TABLE doc.document_chunk (
  chunk_id    BIGSERIAL PRIMARY KEY,
  page_id     BIGINT REFERENCES doc.document_page(page_id),
  chunk_type  TEXT NOT NULL,                 -- prose | table | cell | handwriting
  content     TEXT NOT NULL,
  embedding   vector(768),
  token_count INT
);
CREATE INDEX ix_chunk_hnsw ON doc.document_chunk USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ix_chunk_fts  ON doc.document_chunk USING gin (to_tsvector('english', content));

-- Row-level semantic embeddings (entity linking / NL->SQL grounding)
CREATE TABLE app.row_embedding (
  row_emb_id  BIGSERIAL PRIMARY KEY,
  table_name  TEXT NOT NULL,
  pk_value    BIGINT NOT NULL,
  nl_summary  TEXT NOT NULL,
  embedding   vector(768)
);
CREATE INDEX ix_rowemb_hnsw ON app.row_embedding USING hnsw (embedding vector_cosine_ops);

-- Schema-object embeddings (table/column cards) for schema-linking
CREATE TABLE app.schema_embedding (
  schema_emb_id BIGSERIAL PRIMARY KEY,
  object_type TEXT NOT NULL,                 -- table | column
  fqn         TEXT NOT NULL,                 -- e.g. loan.loan_application.requested_amount
  text        TEXT NOT NULL,                 -- description card (from OpenMetadata)
  embedding   vector(768)
);
CREATE INDEX ix_schemaemb_hnsw ON app.schema_embedding USING hnsw (embedding vector_cosine_ops);
