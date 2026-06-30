-- 90_views.sql — analytics convenience views (candidates should still join base tables)
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
