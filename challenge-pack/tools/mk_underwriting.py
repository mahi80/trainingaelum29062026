#!/usr/bin/env python3
"""Generate a small, learnable auto-loan underwriting dataset (probability-of-default).

Deterministic (numpy seed). Features are the ones an underwriter uses; the binary
`default` label is drawn from a logistic risk model with signal + noise, so a
model can actually learn it (reference AUC ~0.80). Mirrors the public/hidden
pattern used elsewhere:
  example/underwriting/train.csv         (public — train on this)
  example/underwriting/test.csv          (public — self-evaluate: has `default`)
  grading-kit/hidden/underwriting_holdout.csv   (hidden — evaluators score on this)

Feed underwrite_eval.py:  predict pd_score+decision on test.csv -> preds.csv, then
  python eval/underwrite_eval.py --pred preds.csv --labels example/underwriting/test.csv

Run: python tools/mk_underwriting.py --seed 1337
"""
from __future__ import annotations
import argparse
import csv
import os

import numpy as np

FIELDS = ["applicant_id", "fico", "dti", "ltv", "loan_amount", "term_months",
          "vehicle_age", "annual_income", "prior_delinquencies", "inquiries_6mo",
          "down_payment_pct", "employment_years", "region_risk_tier", "default"]
RISK_TIERS = ["low", "medium", "high"]
TIER_W = {"low": -0.3, "medium": 0.0, "high": 0.4}


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


def _make(rng, n, id_prefix):
    fico = rng.integers(520, 830, n)
    dti = np.clip(rng.normal(0.34, 0.10, n), 0.05, 0.7)
    ltv = np.clip(rng.normal(0.95, 0.16, n), 0.5, 1.4)
    loan_amount = rng.integers(5000, 70000, n)
    term = rng.choice([36, 48, 60, 72, 84], n)
    vehicle_age = rng.integers(0, 16, n)
    income = rng.integers(20000, 200000, n)
    prior_delinq = rng.poisson(0.6, n).clip(0, 8)
    inquiries = rng.poisson(1.8, n).clip(0, 12)
    down_pct = np.clip(rng.normal(0.12, 0.08, n), 0.0, 0.5)
    emp_years = np.clip(rng.normal(6, 4, n), 0, 35).round(1)
    tier = rng.choice(RISK_TIERS, n, p=[0.45, 0.4, 0.15])

    # logistic risk model (signal + noise)
    z = (-1.25
         + (680 - fico) / 45.0 * 0.65
         + (dti - 0.35) / 0.10 * 0.55
         + (ltv - 0.90) / 0.15 * 0.45
         + prior_delinq * 0.38
         + inquiries * 0.10
         - down_pct * 2.2
         - emp_years * 0.02
         + np.array([TIER_W[t] for t in tier])
         + rng.normal(0, 0.6, n))
    p = _sigmoid(z)
    default = (rng.random(n) < p).astype(int)

    rows = []
    for i in range(n):
        rows.append({
            "applicant_id": f"{id_prefix}{i:05d}",
            "fico": int(fico[i]), "dti": round(float(dti[i]), 3),
            "ltv": round(float(ltv[i]), 3), "loan_amount": int(loan_amount[i]),
            "term_months": int(term[i]), "vehicle_age": int(vehicle_age[i]),
            "annual_income": int(income[i]), "prior_delinquencies": int(prior_delinq[i]),
            "inquiries_6mo": int(inquiries[i]), "down_payment_pct": round(float(down_pct[i]), 3),
            "employment_years": float(emp_years[i]), "region_risk_tier": tier[i],
            "default": int(default[i]),
        })
    return rows


def _write(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    rate = sum(r["default"] for r in rows) / len(rows)
    print(f"  {path}: {len(rows)} rows, default_rate={rate:.3f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--out", default="example/underwriting")
    ap.add_argument("--hidden", default="../grading-kit/hidden")
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)
    print("underwriting dataset:")
    _write(os.path.join(args.out, "train.csv"), _make(rng, 1000, "TR"))
    _write(os.path.join(args.out, "test.csv"), _make(rng, 250, "TE"))
    _write(os.path.join(args.hidden, "underwriting_holdout.csv"), _make(rng, 300, "HO"))


if __name__ == "__main__":
    main()
