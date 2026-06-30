-- 10_ref.sql — reference / lookup dimension tables (hand-authored)
CREATE TABLE ref.credit_band      (code TEXT PRIMARY KEY, label TEXT NOT NULL, min_fico INT, max_fico INT);
CREATE TABLE ref.loan_product     (code TEXT PRIMARY KEY, label TEXT NOT NULL, max_term_months INT, max_ltv NUMERIC(5,2));
CREATE TABLE ref.application_status(code TEXT PRIMARY KEY, label TEXT NOT NULL, is_terminal BOOLEAN DEFAULT false);
CREATE TABLE ref.decision_outcome (code TEXT PRIMARY KEY, label TEXT NOT NULL);
CREATE TABLE ref.decision_reason  (code TEXT PRIMARY KEY, label TEXT NOT NULL, adverse_action_code TEXT);
CREATE TABLE ref.risk_rating      (code TEXT PRIMARY KEY, label TEXT NOT NULL, pd_floor NUMERIC(6,4), pd_ceiling NUMERIC(6,4));
CREATE TABLE ref.region           (region_id SERIAL PRIMARY KEY, name TEXT NOT NULL, country TEXT NOT NULL, risk_tier TEXT);
CREATE TABLE ref.vehicle_make     (make_id SERIAL PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE ref.vehicle_model    (model_id SERIAL PRIMARY KEY, make_id INT NOT NULL REFERENCES ref.vehicle_make(make_id), name TEXT NOT NULL, body_style TEXT);
CREATE TABLE ref.income_type      (code TEXT PRIMARY KEY, label TEXT NOT NULL, is_verifiable BOOLEAN DEFAULT true);
CREATE TABLE ref.employment_type  (code TEXT PRIMARY KEY, label TEXT NOT NULL);
CREATE TABLE ref.fee_type         (code TEXT PRIMARY KEY, label TEXT NOT NULL, default_amount NUMERIC(10,2));
CREATE TABLE ref.delinquency_bucket(code TEXT PRIMARY KEY, label TEXT NOT NULL, min_days INT, max_days INT);
CREATE TABLE ref.document_type    (code TEXT PRIMARY KEY, label TEXT NOT NULL, doc_class TEXT NOT NULL);
CREATE TABLE ref.channel          (code TEXT PRIMARY KEY, label TEXT NOT NULL);
CREATE TABLE ref.bureau           (code TEXT PRIMARY KEY, label TEXT NOT NULL);
