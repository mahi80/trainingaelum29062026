#!/usr/bin/env python3
"""schemagen — emit the long tail of the auto-loan schema deterministically.

Hand-authored DDL (00/10/20/25/30/80) defines the core + vector tables. This
expander templates the SCD2 history, xref bridge, servicing-detail, and extra
operational tables, plus analytics views, so the warehouse reaches the target
size (>=100 tables / >=200 columns) while staying maintainable.

It then re-scans ALL db/ddl/*.sql and asserts the size targets (also a grading
hook). Output files: 50_history.sql 60_xref.sql 70_servicing.sql 75_extra.sql
90_views.sql

Run: python expand.py --ddl ../../db/ddl
"""
from __future__ import annotations
import argparse
import glob
import os
import re

# (base_table, ref_pk_column) -> SCD2 history table
HISTORY = [
    ("loan.borrower", "borrower_id"), ("loan.party_address", "address_id"),
    ("loan.loan_application", "application_id"), ("loan.vehicle", "vehicle_id"),
    ("loan.loan_terms", "loan_terms_id"), ("loan.rate_tier", "rate_tier_id"),
    ("loan.underwriting_decision", "decision_id"), ("loan.policy_rule", "policy_rule_id"),
    ("loan.income_source", "income_source_id"), ("loan.employment_record", "employment_id"),
    ("loan.loan", "loan_id"), ("loan.collateral_valuation", "valuation_id"),
]

# bridge / many-to-many tables: (name, (col, ref_table.ref_col) ...)
XREF = [
    ("decision_reason_xref", [("decision_id", "loan.underwriting_decision(decision_id)"),
                              ("reason_code", "ref.decision_reason(code)")]),
    ("application_stipulation_xref", [("application_id", "loan.loan_application(application_id)"),
                                      ("stipulation_id", "loan.stipulation(stipulation_id)")]),
    ("policy_rule_region_xref", [("policy_rule_id", "loan.policy_rule(policy_rule_id)"),
                                 ("region_id", "ref.region(region_id)")]),
    ("borrower_document_xref", [("borrower_id", "loan.borrower(borrower_id)"),
                                ("document_id", "doc.document(document_id)")]),
    ("application_document_xref", [("application_id", "loan.loan_application(application_id)"),
                                   ("document_id", "doc.document(document_id)")]),
    ("loan_fee_xref", [("loan_id", "loan.loan(loan_id)"),
                       ("fee_code", "ref.fee_type(code)")]),
    ("rate_sheet_region_xref", [("rate_sheet_id", "loan.rate_sheet(rate_sheet_id)"),
                                ("region_id", "ref.region(region_id)")]),
    ("underwriter_product_xref", [("underwriter_id", "loan.underwriter(underwriter_id)"),
                                  ("product_code", "ref.loan_product(code)")]),
]

# servicing-detail tables: (name, columns_sql)
SERVICING = [
    ("amortization_schedule", "loan_id BIGINT REFERENCES loan.loan(loan_id), period INT, due_date DATE, principal NUMERIC(12,2), interest NUMERIC(12,2), balance NUMERIC(14,2)"),
    ("payment_allocation", "payment_id BIGINT REFERENCES loan.payment(payment_id), to_principal NUMERIC(12,2), to_interest NUMERIC(12,2), to_fees NUMERIC(12,2)"),
    ("escrow", "loan_id BIGINT REFERENCES loan.loan(loan_id), balance NUMERIC(12,2), monthly_amount NUMERIC(12,2), as_of_date DATE"),
    ("late_fee", "loan_id BIGINT REFERENCES loan.loan(loan_id), assessed_date DATE, amount NUMERIC(10,2), waived BOOLEAN DEFAULT false"),
    ("payoff", "loan_id BIGINT REFERENCES loan.loan(loan_id), quote_date DATE, payoff_amount NUMERIC(14,2), good_through DATE"),
    ("charge_off", "loan_id BIGINT REFERENCES loan.loan(loan_id), charged_off_date DATE, amount NUMERIC(14,2), recovery NUMERIC(14,2)"),
    ("repossession", "loan_id BIGINT REFERENCES loan.loan(loan_id), repo_date DATE, sale_amount NUMERIC(14,2), deficiency NUMERIC(14,2)"),
    ("insurance_policy", "loan_id BIGINT REFERENCES loan.loan(loan_id), carrier TEXT, policy_no TEXT, premium NUMERIC(10,2), expires DATE"),
    ("gap_coverage", "loan_id BIGINT REFERENCES loan.loan(loan_id), provider TEXT, cost NUMERIC(10,2), active BOOLEAN DEFAULT true"),
    ("ach_mandate", "borrower_id BIGINT REFERENCES loan.borrower(borrower_id), bank_token TEXT, status TEXT, created_at TIMESTAMPTZ DEFAULT now()"),
    ("statement", "loan_id BIGINT REFERENCES loan.loan(loan_id), period_start DATE, period_end DATE, amount_due NUMERIC(12,2)"),
    ("dispute", "loan_id BIGINT REFERENCES loan.loan(loan_id), opened_date DATE, category TEXT, status TEXT, resolved_date DATE"),
]

# extra operational tables (round out a realistic warehouse)
EXTRA = [
    ("notification", "user_id BIGINT, channel TEXT, body TEXT, sent_at TIMESTAMPTZ, read_at TIMESTAMPTZ"),
    ("communication_log", "borrower_id BIGINT REFERENCES loan.borrower(borrower_id), direction TEXT, medium TEXT, summary TEXT, at TIMESTAMPTZ DEFAULT now()"),
    ("task", "application_id BIGINT REFERENCES loan.loan_application(application_id), title TEXT, assignee TEXT, status TEXT, due_date DATE"),
    ("queue_assignment", "application_id BIGINT REFERENCES loan.loan_application(application_id), queue TEXT, assigned_to TEXT, assigned_at TIMESTAMPTZ DEFAULT now()"),
    ("sla_event", "application_id BIGINT REFERENCES loan.loan_application(application_id), sla TEXT, breached BOOLEAN, at TIMESTAMPTZ DEFAULT now()"),
    ("fraud_flag", "application_id BIGINT REFERENCES loan.loan_application(application_id), rule TEXT, severity TEXT, cleared BOOLEAN DEFAULT false"),
    ("kyc_check", "party_id BIGINT REFERENCES loan.party(party_id), provider TEXT, result TEXT, checked_at TIMESTAMPTZ"),
    ("aml_screening", "party_id BIGINT REFERENCES loan.party(party_id), list_name TEXT, hit BOOLEAN, screened_at TIMESTAMPTZ"),
    ("consent", "party_id BIGINT REFERENCES loan.party(party_id), consent_type TEXT, granted BOOLEAN, at TIMESTAMPTZ DEFAULT now()"),
    ("disclosure", "application_id BIGINT REFERENCES loan.loan_application(application_id), kind TEXT, delivered_at TIMESTAMPTZ"),
    ("e_sign_event", "document_id BIGINT REFERENCES doc.document(document_id), signer TEXT, signed_at TIMESTAMPTZ, ip INET"),
    ("adverse_action_notice", "decision_id BIGINT REFERENCES loan.underwriting_decision(decision_id), reason_code TEXT REFERENCES ref.decision_reason(code), sent_at TIMESTAMPTZ"),
    ("pricing_exception", "application_id BIGINT REFERENCES loan.loan_application(application_id), requested_apr NUMERIC(6,3), approved_apr NUMERIC(6,3), approver TEXT"),
    ("dealer", "name TEXT NOT NULL, region_id INT REFERENCES ref.region(region_id), rating TEXT"),
    ("dealer_contract", "dealer_id BIGINT REFERENCES loan.dealer(dealer_id), start_date DATE, end_date DATE, reserve_pct NUMERIC(5,2)"),
    ("funding_event", "loan_id BIGINT REFERENCES loan.loan(loan_id), funded_at TIMESTAMPTZ, amount NUMERIC(14,2), method TEXT"),
    ("title_record", "vehicle_id BIGINT REFERENCES loan.vehicle(vehicle_id), state TEXT, title_no TEXT, status TEXT"),
    ("lien", "vehicle_id BIGINT REFERENCES loan.vehicle(vehicle_id), holder TEXT, perfected_date DATE, released BOOLEAN DEFAULT false"),
    ("gps_device", "vehicle_id BIGINT REFERENCES loan.vehicle(vehicle_id), serial_no TEXT, installed_at TIMESTAMPTZ, active BOOLEAN DEFAULT true"),
    ("telematics_reading", "gps_device_id BIGINT REFERENCES loan.gps_device(gps_device_id), read_at TIMESTAMPTZ, lat NUMERIC(9,6), lon NUMERIC(9,6), odometer INT"),
]

VIEWS = """-- 90_views.sql — analytics convenience views (candidates should still join base tables)
CREATE OR REPLACE VIEW loan.vw_application_360 AS
SELECT a.application_id, a.application_no, a.status, a.requested_amount,
       p.first_name, p.last_name, b.credit_band, br.name AS branch,
       v.vin, v.fuel_type, d.decision, d.dti_ratio, d.ltv_ratio, d.pd_score
FROM loan.loan_application a
JOIN loan.borrower b ON b.borrower_id = a.borrower_id
JOIN loan.party p ON p.party_id = b.party_id
JOIN loan.branch br ON br.branch_id = a.branch_id
LEFT JOIN loan.vehicle v ON v.vehicle_id = a.vehicle_id
LEFT JOIN loan.underwriting_decision d ON d.application_id = a.application_id;

CREATE OR REPLACE VIEW loan.vw_underwriting_funnel AS
SELECT br.name AS branch, a.status, COUNT(*) AS n
FROM loan.loan_application a JOIN loan.branch br ON br.branch_id = a.branch_id
GROUP BY br.name, a.status;

CREATE OR REPLACE VIEW loan.vw_branch_performance AS
SELECT br.name AS branch, COUNT(l.loan_id) AS loans, SUM(l.principal) AS principal
FROM loan.branch br
LEFT JOIN loan.loan_application a ON a.branch_id = br.branch_id
LEFT JOIN loan.loan l ON l.application_id = a.application_id
GROUP BY br.name;

CREATE OR REPLACE VIEW loan.vw_delinquency_summary AS
SELECT db.label AS bucket, COUNT(*) AS n
FROM loan.delinquency dq JOIN ref.delinquency_bucket db ON db.code = dq.bucket_code
GROUP BY db.label;

CREATE OR REPLACE VIEW loan.vw_collateral_ltv AS
SELECT a.application_id, a.requested_amount, cv.value_amount,
       ROUND(a.requested_amount / NULLIF(cv.value_amount,0), 4) AS ltv
FROM loan.loan_application a
JOIN loan.vehicle v ON v.vehicle_id = a.vehicle_id
JOIN LATERAL (SELECT value_amount FROM loan.collateral_valuation c
              WHERE c.vehicle_id = v.vehicle_id ORDER BY valuation_date DESC LIMIT 1) cv ON true;

CREATE OR REPLACE VIEW loan.vw_income_summary AS
SELECT b.borrower_id, SUM(i.monthly_amount) AS monthly_income
FROM loan.borrower b JOIN loan.income_source i ON i.borrower_id = b.borrower_id
GROUP BY b.borrower_id;
"""


def history_sql():
    out = ["-- 50_history.sql — SCD2 history tables (generated by schemagen)"]
    for base, pk in HISTORY:
        schema, tbl = base.split(".")
        out.append(f"""CREATE TABLE {schema}.{tbl}_history (
  history_id    BIGSERIAL PRIMARY KEY,
  {pk}        BIGINT NOT NULL REFERENCES {base}({pk}),
  valid_from    TIMESTAMPTZ NOT NULL DEFAULT now(),
  valid_to      TIMESTAMPTZ,
  is_current    BOOLEAN NOT NULL DEFAULT true,
  changed_by    TEXT,
  change_reason TEXT,
  snapshot      JSONB
);""")
    return "\n".join(out) + "\n"


def xref_sql():
    out = ["-- 60_xref.sql — many-to-many bridge tables (generated by schemagen)"]
    for name, cols in XREF:
        coldefs = ",\n  ".join(f"{c} BIGINT REFERENCES {ref}" if not c.endswith("code") and not c.endswith("region_id")
                               else f"{c} {'INT' if c.endswith('region_id') else 'TEXT'} REFERENCES {ref}"
                               for c, ref in cols)
        pk = ", ".join(c for c, _ in cols)
        out.append(f"""CREATE TABLE loan.{name} (
  {coldefs},
  weight NUMERIC(6,3),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY ({pk})
);""")
    return "\n".join(out) + "\n"


def detail_sql(fname, title, specs):
    out = [f"-- {fname} — {title} (generated by schemagen)"]
    for name, cols in specs:
        out.append(f"CREATE TABLE loan.{name} (\n  {name}_id BIGSERIAL PRIMARY KEY,\n  {cols}\n);")
    return "\n".join(out) + "\n"


def _split_top_level(body):
    """Split a CREATE TABLE body on commas at parenthesis depth 0."""
    parts, depth, cur = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur))
    return parts


def count_targets(ddl_dir):
    tables, columns = 0, 0
    skip = ("PRIMARY", "FOREIGN", "UNIQUE", "CONSTRAINT", "CHECK")
    for path in sorted(glob.glob(os.path.join(ddl_dir, "*.sql"))):
        sql = open(path, encoding="utf-8").read()
        # body = text between the table's opening '(' and the terminating ');'
        for m in re.finditer(r"CREATE TABLE\s+[\w.\"]+\s*\((.*?)\)\s*;",
                             sql, re.DOTALL | re.IGNORECASE):
            tables += 1
            for seg in _split_top_level(m.group(1)):
                s = seg.strip()
                if s and s.split()[0].upper() not in skip:
                    columns += 1
    return tables, columns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ddl", default="../../db/ddl")
    args = ap.parse_args()
    d = args.ddl
    open(os.path.join(d, "50_history.sql"), "w", encoding="utf-8").write(history_sql())
    open(os.path.join(d, "60_xref.sql"), "w", encoding="utf-8").write(xref_sql())
    open(os.path.join(d, "70_servicing.sql"), "w", encoding="utf-8").write(
        detail_sql("70_servicing.sql", "servicing-detail tables", SERVICING))
    open(os.path.join(d, "75_extra.sql"), "w", encoding="utf-8").write(
        detail_sql("75_extra.sql", "extra operational tables", EXTRA))
    open(os.path.join(d, "90_views.sql"), "w", encoding="utf-8").write(VIEWS)

    tables, columns = count_targets(d)
    print(f"DDL files in {d}: tables={tables} columns={columns}")
    assert tables >= 100, f"need >=100 tables, got {tables}"
    assert columns >= 200, f"need >=200 columns, got {columns}"
    print("OK: schema meets size targets (>=100 tables, >=200 columns)")


if __name__ == "__main__":
    main()
