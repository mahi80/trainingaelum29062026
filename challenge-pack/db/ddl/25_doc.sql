-- 25_doc.sql — documents / pages / extractions / audit (links schema to example/ scans)
CREATE TABLE doc.document (
  document_id   BIGSERIAL PRIMARY KEY,
  doc_class     TEXT NOT NULL,
  doc_type_code TEXT REFERENCES ref.document_type(code),
  application_id BIGINT REFERENCES loan.loan_application(application_id),
  source_path   TEXT NOT NULL,
  page_count    INT NOT NULL,
  ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE doc.document_page (
  page_id       BIGSERIAL PRIMARY KEY,
  document_id   BIGINT NOT NULL REFERENCES doc.document(document_id),
  page_no       INT NOT NULL,
  image_path    TEXT NOT NULL,
  UNIQUE (document_id, page_no)
);
CREATE TABLE doc.document_extraction (
  extraction_id BIGSERIAL PRIMARY KEY,
  page_id       BIGINT NOT NULL REFERENCES doc.document_page(page_id),
  extractor     TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE doc.extraction_cell (
  cell_id       BIGSERIAL PRIMARY KEY,
  extraction_id BIGINT NOT NULL REFERENCES doc.document_extraction(extraction_id),
  row_start INT, row_end INT, col_start INT, col_end INT,
  text TEXT, bbox INT[], is_handwritten BOOLEAN DEFAULT false
);
CREATE TABLE doc.audit_log (
  audit_id      BIGSERIAL PRIMARY KEY,
  actor TEXT, action TEXT, entity TEXT, entity_id BIGINT,
  at TIMESTAMPTZ NOT NULL DEFAULT now(), detail JSONB
);
