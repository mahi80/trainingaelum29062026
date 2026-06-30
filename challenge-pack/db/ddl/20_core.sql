-- 20_core.sql — operational auto-loan tables (hand-authored, normalized for multi-join NL->SQL)

-- org
CREATE TABLE loan.branch (
  branch_id     BIGSERIAL PRIMARY KEY,
  name          TEXT NOT NULL,
  region_id     INT REFERENCES ref.region(region_id),
  opened_date   DATE,
  is_active     BOOLEAN NOT NULL DEFAULT true
);
CREATE TABLE loan.loan_officer (
  loan_officer_id BIGSERIAL PRIMARY KEY,
  full_name     TEXT NOT NULL,
  branch_id     BIGINT REFERENCES loan.branch(branch_id),
  hired_date    DATE
);
CREATE TABLE loan.underwriter (
  underwriter_id BIGSERIAL PRIMARY KEY,
  full_name     TEXT NOT NULL,
  authority_limit NUMERIC(14,2),
  branch_id     BIGINT REFERENCES loan.branch(branch_id)
);

-- party / borrower
CREATE TABLE loan.party (
  party_id      BIGSERIAL PRIMARY KEY,
  party_type    TEXT NOT NULL CHECK (party_type IN ('individual','business')),
  first_name    TEXT, last_name TEXT, legal_name TEXT,
  dob           DATE,
  ssn_tokenized TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE loan.party_address (
  address_id    BIGSERIAL PRIMARY KEY,
  party_id      BIGINT NOT NULL REFERENCES loan.party(party_id),
  line1 TEXT, city TEXT, state TEXT, postal_code TEXT, country TEXT,
  is_current    BOOLEAN NOT NULL DEFAULT true
);
CREATE TABLE loan.party_contact (
  contact_id    BIGSERIAL PRIMARY KEY,
  party_id      BIGINT NOT NULL REFERENCES loan.party(party_id),
  email TEXT, phone TEXT, preferred BOOLEAN DEFAULT false
);
CREATE TABLE loan.borrower (
  borrower_id   BIGSERIAL PRIMARY KEY,
  party_id      BIGINT NOT NULL REFERENCES loan.party(party_id),
  credit_band   TEXT REFERENCES ref.credit_band(code),
  primary_branch_id BIGINT REFERENCES loan.branch(branch_id),
  kyc_status    TEXT NOT NULL DEFAULT 'pending'
);

-- employment & income
CREATE TABLE loan.employer (
  employer_id   BIGSERIAL PRIMARY KEY,
  name          TEXT NOT NULL, industry TEXT, region_id INT REFERENCES ref.region(region_id)
);
CREATE TABLE loan.employment_record (
  employment_id BIGSERIAL PRIMARY KEY,
  borrower_id   BIGINT NOT NULL REFERENCES loan.borrower(borrower_id),
  employer_id   BIGINT REFERENCES loan.employer(employer_id),
  employment_type TEXT REFERENCES ref.employment_type(code),
  start_date DATE, end_date DATE, monthly_income NUMERIC(12,2)
);
CREATE TABLE loan.employment_verification (
  emp_verif_id  BIGSERIAL PRIMARY KEY,
  employment_id BIGINT NOT NULL REFERENCES loan.employment_record(employment_id),
  verified_on DATE, method TEXT, verified_income NUMERIC(12,2), status TEXT
);
CREATE TABLE loan.income_source (
  income_source_id BIGSERIAL PRIMARY KEY,
  borrower_id   BIGINT NOT NULL REFERENCES loan.borrower(borrower_id),
  income_type   TEXT REFERENCES ref.income_type(code),
  monthly_amount NUMERIC(12,2), is_primary BOOLEAN DEFAULT false
);
CREATE TABLE loan.income_verification (
  income_verif_id BIGSERIAL PRIMARY KEY,
  income_source_id BIGINT NOT NULL REFERENCES loan.income_source(income_source_id),
  verified_on DATE, verified_amount NUMERIC(12,2), status TEXT
);

-- vehicle / collateral
CREATE TABLE loan.vehicle (
  vehicle_id    BIGSERIAL PRIMARY KEY,
  vin           TEXT UNIQUE,
  make_id       INT REFERENCES ref.vehicle_make(make_id),
  model_id      INT REFERENCES ref.vehicle_model(model_id),
  model_year    INT, fuel_type TEXT, condition TEXT CHECK (condition IN ('new','used','cpo')),
  mileage       INT, msrp NUMERIC(14,2), purchase_price NUMERIC(14,2)
);
CREATE TABLE loan.collateral_valuation (
  valuation_id  BIGSERIAL PRIMARY KEY,
  vehicle_id    BIGINT NOT NULL REFERENCES loan.vehicle(vehicle_id),
  source TEXT, valuation_date DATE NOT NULL, value_amount NUMERIC(14,2) NOT NULL
);

-- application
CREATE TABLE loan.loan_application (
  application_id BIGSERIAL PRIMARY KEY,
  application_no TEXT UNIQUE NOT NULL,
  borrower_id    BIGINT NOT NULL REFERENCES loan.borrower(borrower_id),
  co_borrower_id BIGINT REFERENCES loan.borrower(borrower_id),
  branch_id      BIGINT NOT NULL REFERENCES loan.branch(branch_id),
  loan_officer_id BIGINT REFERENCES loan.loan_officer(loan_officer_id),
  product_code   TEXT NOT NULL REFERENCES ref.loan_product(code),
  channel_code   TEXT REFERENCES ref.channel(code),
  vehicle_id     BIGINT REFERENCES loan.vehicle(vehicle_id),
  requested_amount NUMERIC(14,2) NOT NULL,
  requested_term_months INT NOT NULL,
  status         TEXT NOT NULL REFERENCES ref.application_status(code),
  submitted_at TIMESTAMPTZ, decided_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_app_borrower ON loan.loan_application(borrower_id);
CREATE INDEX ix_app_branch_status ON loan.loan_application(branch_id, status);

-- credit bureau
CREATE TABLE loan.credit_pull (
  credit_pull_id BIGSERIAL PRIMARY KEY,
  borrower_id   BIGINT NOT NULL REFERENCES loan.borrower(borrower_id),
  bureau_code   TEXT REFERENCES ref.bureau(code),
  pulled_at TIMESTAMPTZ NOT NULL, fico_score INT, vantage_score INT
);
CREATE TABLE loan.tradeline (
  tradeline_id  BIGSERIAL PRIMARY KEY,
  credit_pull_id BIGINT NOT NULL REFERENCES loan.credit_pull(credit_pull_id),
  creditor_name TEXT, account_type TEXT, balance NUMERIC(14,2),
  monthly_payment NUMERIC(12,2), status TEXT, opened_date DATE
);
CREATE TABLE loan.credit_inquiry (
  inquiry_id    BIGSERIAL PRIMARY KEY,
  credit_pull_id BIGINT NOT NULL REFERENCES loan.credit_pull(credit_pull_id),
  inquiry_date DATE, creditor TEXT, purpose TEXT
);

-- underwriting
CREATE TABLE loan.underwriting_decision (
  decision_id   BIGSERIAL PRIMARY KEY,
  application_id BIGINT NOT NULL REFERENCES loan.loan_application(application_id),
  underwriter_id BIGINT REFERENCES loan.underwriter(underwriter_id),
  decision      TEXT NOT NULL REFERENCES ref.decision_outcome(code),
  approved_amount NUMERIC(14,2), approved_apr NUMERIC(6,3),
  dti_ratio NUMERIC(6,3), ltv_ratio NUMERIC(6,3),
  pd_score NUMERIC(6,4), risk_rating TEXT REFERENCES ref.risk_rating(code),
  policy_doc_id BIGINT, decided_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE loan.exception_approval (
  exception_id  BIGSERIAL PRIMARY KEY,
  decision_id   BIGINT NOT NULL REFERENCES loan.underwriting_decision(decision_id),
  reason TEXT, approved_by BIGINT REFERENCES loan.underwriter(underwriter_id), approved_at TIMESTAMPTZ
);
CREATE TABLE loan.stipulation (
  stipulation_id BIGSERIAL PRIMARY KEY,
  application_id BIGINT NOT NULL REFERENCES loan.loan_application(application_id),
  description TEXT, status TEXT DEFAULT 'open', cleared_at TIMESTAMPTZ
);

-- policy
CREATE TABLE loan.policy_document (
  policy_doc_id BIGSERIAL PRIMARY KEY,
  title TEXT NOT NULL, version TEXT NOT NULL,
  effective_date DATE NOT NULL, retired_date DATE, document_id BIGINT
);
CREATE TABLE loan.policy_rule (
  policy_rule_id BIGSERIAL PRIMARY KEY,
  policy_doc_id BIGINT NOT NULL REFERENCES loan.policy_document(policy_doc_id),
  rule_code TEXT NOT NULL, description TEXT,
  supersedes_rule_id BIGINT REFERENCES loan.policy_rule(policy_rule_id),
  region_id INT REFERENCES ref.region(region_id)
);
CREATE TABLE loan.rate_sheet (
  rate_sheet_id BIGSERIAL PRIMARY KEY,
  product_code TEXT REFERENCES ref.loan_product(code),
  effective_date DATE NOT NULL, retired_date DATE
);
CREATE TABLE loan.rate_tier (
  rate_tier_id  BIGSERIAL PRIMARY KEY,
  rate_sheet_id BIGINT NOT NULL REFERENCES loan.rate_sheet(rate_sheet_id),
  credit_band TEXT REFERENCES ref.credit_band(code),
  ltv_max NUMERIC(5,2), apr NUMERIC(6,3)
);

-- loan / servicing (core)
CREATE TABLE loan.loan (
  loan_id       BIGSERIAL PRIMARY KEY,
  application_id BIGINT NOT NULL REFERENCES loan.loan_application(application_id),
  principal NUMERIC(14,2) NOT NULL, apr NUMERIC(6,3) NOT NULL,
  term_months INT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
  booked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE loan.loan_terms (
  loan_terms_id BIGSERIAL PRIMARY KEY,
  loan_id BIGINT NOT NULL REFERENCES loan.loan(loan_id),
  monthly_payment NUMERIC(12,2), first_payment_date DATE, maturity_date DATE
);
CREATE TABLE loan.payment (
  payment_id    BIGSERIAL PRIMARY KEY,
  loan_id BIGINT NOT NULL REFERENCES loan.loan(loan_id),
  due_date DATE, paid_date DATE, amount NUMERIC(12,2), is_late BOOLEAN DEFAULT false
);
CREATE TABLE loan.delinquency (
  delinquency_id BIGSERIAL PRIMARY KEY,
  loan_id BIGINT NOT NULL REFERENCES loan.loan(loan_id),
  bucket_code TEXT REFERENCES ref.delinquency_bucket(code),
  as_of_date DATE, days_past_due INT
);
