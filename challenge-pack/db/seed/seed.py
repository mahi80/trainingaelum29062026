#!/usr/bin/env python3
"""Deterministic Faker seed for the auto-loan warehouse.

Populates reference + operational tables with referentially-consistent data and
maps every document/page in `example/manifest.jsonl` to doc.document /
doc.document_page rows pointing at the scanned images — so the structured DB and
the scans describe the SAME loans (enables cross-modal questions).

Usage:
  DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres \
    python seed.py --seed 1337 --example ../../example
  python seed.py --dry-run --example ../../example   # validate plan, no DB

Snapshot a fixed dump after seeding:
  pg_dump --no-owner $DATABASE_URL | gzip > seed_fixed.sql.gz
"""
from __future__ import annotations
import argparse
import json
import os
import random

from faker import Faker

REGIONS = [("Pune", "IN", "medium"), ("Austin", "US", "low"), ("Leeds", "GB", "medium"),
           ("Berlin", "DE", "low"), ("Toronto", "CA", "low"), ("Dublin", "IE", "medium")]
CREDIT_BANDS = [("SUBPRIME", "Subprime", 300, 619), ("NEARPRIME", "Near-prime", 620, 659),
                ("PRIME", "Prime", 660, 739), ("SUPERPRIME", "Super-prime", 740, 850)]
PRODUCTS = [("AUTO_NEW", "New Auto", 84, 120.0), ("AUTO_USED", "Used Auto", 72, 110.0),
            ("AUTO_REFI", "Auto Refinance", 72, 100.0), ("AUTO_LEASE", "Lease Buyout", 60, 105.0)]
APP_STATUS = [("submitted", "Submitted", False), ("in_review", "In Review", False),
              ("approved", "Approved", True), ("declined", "Declined", True),
              ("funded", "Funded", True), ("withdrawn", "Withdrawn", True)]
OUTCOMES = [("approve", "Approve"), ("decline", "Decline"), ("counter", "Counter-offer"),
            ("refer", "Refer to UW")]
REASONS = [("DTI_HIGH", "Debt-to-income too high", "R07"), ("LTV_HIGH", "Loan-to-value too high", "R12"),
           ("THIN_FILE", "Insufficient credit history", "R03"), ("INCOME_UNVERIFIED", "Income not verified", "R05"),
           ("DEROG", "Derogatory tradelines", "R02"), ("POLICY_OK", "Within policy", "R00")]
RISK = [("A", "Low", 0.0, 0.03), ("B", "Moderate", 0.03, 0.08),
        ("C", "Elevated", 0.08, 0.18), ("D", "High", 0.18, 1.0)]
INCOME_TYPES = [("W2", "W-2 Salary", True), ("SELF", "Self-employed", True),
                ("GIG", "Gig / 1099", True), ("OTHER", "Other", False)]
EMP_TYPES = [("FT", "Full-time"), ("PT", "Part-time"), ("CONTRACT", "Contract"), ("SELF", "Self-employed")]
FEE_TYPES = [("ORIG", "Origination", 295.0), ("DOC", "Documentation", 85.0), ("LATE", "Late fee", 25.0)]
DELINQ = [("CUR", "Current", 0, 0), ("DPD30", "30 DPD", 1, 30), ("DPD60", "60 DPD", 31, 60),
          ("DPD90", "90+ DPD", 61, 9999)]
DOC_TYPES = [("CREDIT_APP", "Credit Application", "application"),
             ("BANK_STMT", "Bank Statement", "verification"),
             ("RATE_SHEET", "Rate Sheet", "policy")]
CHANNELS = [("BRANCH", "Branch"), ("ONLINE", "Online"), ("DEALER", "Dealer")]
BUREAUS = [("EXP", "Experian"), ("EQF", "Equifax"), ("TU", "TransUnion")]
MAKES = ["Toyota", "Honda", "Tesla", "Ford", "Hyundai", "Kia", "Nissan", "BMW"]
MODELS = {"Toyota": ["Corolla", "RAV4"], "Honda": ["Civic", "CR-V"], "Tesla": ["Model 3", "Model Y"],
          "Ford": ["F-150", "Escape"], "Hyundai": ["Elantra", "Tucson"], "Kia": ["Sportage", "Niro"],
          "Nissan": ["Leaf", "Altima"], "BMW": ["320i", "X3"]}
DOCCLASS_TO_TYPE = {"application": "CREDIT_APP", "verification": "BANK_STMT", "policy": "RATE_SHEET"}


class DB:
    """Thin psycopg wrapper; in --dry-run, becomes a no-op counter."""
    def __init__(self, dsn, dry):
        self.dry = dry
        self.counts = {}
        if dry:
            self.conn = None
            self._fake_id = 0
        else:
            import psycopg
            self.conn = psycopg.connect(dsn, autocommit=False)
            self.cur = self.conn.cursor()

    def ins(self, table, returning="", **cols):
        self.counts[table] = self.counts.get(table, 0) + 1
        if self.dry:
            self._fake_id += 1
            return self._fake_id if returning else None
        keys = ",".join(cols)
        ph = ",".join(["%s"] * len(cols))
        sql = f"INSERT INTO {table} ({keys}) VALUES ({ph})"
        if returning:
            sql += f" RETURNING {returning}"
        self.cur.execute(sql, list(cols.values()))
        return self.cur.fetchone()[0] if returning else None

    def commit(self):
        if not self.dry:
            self.conn.commit()


def seed_reference(db):
    for code, label, lo, hi in CREDIT_BANDS:
        db.ins("ref.credit_band", code=code, label=label, min_fico=lo, max_fico=hi)
    for code, label, term, ltv in PRODUCTS:
        db.ins("ref.loan_product", code=code, label=label, max_term_months=term, max_ltv=ltv)
    for code, label, term in APP_STATUS:
        db.ins("ref.application_status", code=code, label=label, is_terminal=term)
    for code, label in OUTCOMES:
        db.ins("ref.decision_outcome", code=code, label=label)
    for code, label, aa in REASONS:
        db.ins("ref.decision_reason", code=code, label=label, adverse_action_code=aa)
    for code, label, lo, hi in RISK:
        db.ins("ref.risk_rating", code=code, label=label, pd_floor=lo, pd_ceiling=hi)
    region_ids = [db.ins("ref.region", returning="region_id", name=n, country=c, risk_tier=t)
                  for n, c, t in REGIONS]
    for code, label, v in INCOME_TYPES:
        db.ins("ref.income_type", code=code, label=label, is_verifiable=v)
    for code, label in EMP_TYPES:
        db.ins("ref.employment_type", code=code, label=label)
    for code, label, amt in FEE_TYPES:
        db.ins("ref.fee_type", code=code, label=label, default_amount=amt)
    for code, label, lo, hi in DELINQ:
        db.ins("ref.delinquency_bucket", code=code, label=label, min_days=lo, max_days=hi)
    for code, label, dc in DOC_TYPES:
        db.ins("ref.document_type", code=code, label=label, doc_class=dc)
    for code, label in CHANNELS:
        db.ins("ref.channel", code=code, label=label)
    for code, label in BUREAUS:
        db.ins("ref.bureau", code=code, label=label)
    make_ids, model_ids = {}, []
    for mk in MAKES:
        mid = db.ins("ref.vehicle_make", returning="make_id", name=mk)
        make_ids[mk] = mid
        for md in MODELS[mk]:
            model_ids.append((mid, db.ins("ref.vehicle_model", returning="model_id",
                                          make_id=mid, name=md, body_style="sedan")))
    return region_ids, make_ids, model_ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get(
        "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres"))
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--example", default="../../example")
    ap.add_argument("--borrowers", type=int, default=600)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    fake = Faker()
    Faker.seed(args.seed)
    db = DB(args.dsn, args.dry_run)

    region_ids, make_ids, model_ids = seed_reference(db)

    # org
    branches = []
    for i, (city, _, _) in enumerate(REGIONS):
        branches.append(db.ins("loan.branch", returning="branch_id", name=f"{city} Branch",
                               region_id=region_ids[i], opened_date=fake.date_this_decade(),
                               is_active=True))
    officers = [db.ins("loan.loan_officer", returning="loan_officer_id", full_name=fake.name(),
                       branch_id=rng.choice(branches), hired_date=fake.date_this_decade())
                for _ in range(18)]
    underwriters = [db.ins("loan.underwriter", returning="underwriter_id", full_name=fake.name(),
                           authority_limit=rng.choice([50000, 100000, 250000]),
                           branch_id=rng.choice(branches)) for _ in range(10)]

    # borrowers + supporting
    borrowers, vehicles = [], []
    employer_ids = [db.ins("loan.employer", returning="employer_id", name=fake.company(),
                           industry=fake.bs().split()[0], region_id=rng.choice(region_ids))
                    for _ in range(40)]
    for _ in range(args.borrowers):
        pid = db.ins("loan.party", returning="party_id", party_type="individual",
                     first_name=fake.first_name(), last_name=fake.last_name(),
                     dob=fake.date_of_birth(minimum_age=21, maximum_age=70),
                     ssn_tokenized=f"TKN-{rng.randint(100000,999999)}")
        db.ins("loan.party_address", party_id=pid, line1=fake.street_address(),
               city=fake.city(), state=fake.state_abbr() if hasattr(fake, 'state_abbr') else "NA",
               postal_code=fake.postcode(), country="US", is_current=True)
        db.ins("loan.party_contact", party_id=pid, email=fake.email(), phone=fake.msisdn(), preferred=True)
        bid = db.ins("loan.borrower", returning="borrower_id", party_id=pid,
                     credit_band=rng.choice([c[0] for c in CREDIT_BANDS]),
                     primary_branch_id=rng.choice(branches), kyc_status="verified")
        borrowers.append(bid)
        emp = db.ins("loan.employment_record", returning="employment_id", borrower_id=bid,
                     employer_id=rng.choice(employer_ids), employment_type=rng.choice([e[0] for e in EMP_TYPES]),
                     start_date=fake.date_this_decade(), monthly_income=rng.randint(2500, 14000))
        db.ins("loan.employment_verification", employment_id=emp, verified_on=fake.date_this_year(),
               method="paystub", verified_income=rng.randint(2500, 14000), status="verified")
        isrc = db.ins("loan.income_source", returning="income_source_id", borrower_id=bid,
                      income_type=rng.choice([i[0] for i in INCOME_TYPES]),
                      monthly_amount=rng.randint(2500, 14000), is_primary=True)
        db.ins("loan.income_verification", income_source_id=isrc, verified_on=fake.date_this_year(),
               verified_amount=rng.randint(2500, 14000), status="verified")
        cp = db.ins("loan.credit_pull", returning="credit_pull_id", borrower_id=bid,
                    bureau_code=rng.choice([b[0] for b in BUREAUS]),
                    pulled_at=fake.date_time_this_year(), fico_score=rng.randint(560, 820),
                    vantage_score=rng.randint(560, 820))
        for _ in range(rng.randint(1, 4)):
            db.ins("loan.tradeline", credit_pull_id=cp, creditor_name=fake.company(),
                   account_type=rng.choice(["card", "auto", "mortgage", "student"]),
                   balance=rng.randint(0, 40000), monthly_payment=rng.randint(0, 900),
                   status=rng.choice(["open", "closed"]), opened_date=fake.date_this_decade())
        mk = rng.choice(MAKES)
        mid_model = rng.choice([m for (mkid, m) in model_ids if mkid == make_ids[mk]])
        vid = db.ins("loan.vehicle", returning="vehicle_id", vin=fake.bothify("?#?#?####??#####").upper(),
                     make_id=make_ids[mk], model_id=mid_model, model_year=rng.randint(2015, 2025),
                     fuel_type=rng.choice(["ICE", "Hybrid", "EV"]),
                     condition=rng.choice(["new", "used", "cpo"]), mileage=rng.randint(0, 90000),
                     msrp=rng.randint(18000, 60000), purchase_price=rng.randint(15000, 58000))
        db.ins("loan.collateral_valuation", vehicle_id=vid, source="KBB",
               valuation_date=fake.date_this_year(), value_amount=rng.randint(12000, 55000))
        vehicles.append(vid)

    # applications + decisions + loans + payments
    n_app = int(args.borrowers * 1.2)
    for i in range(n_app):
        bid = rng.choice(borrowers)
        status = rng.choices([s[0] for s in APP_STATUS], weights=[2, 2, 4, 2, 3, 1])[0]
        appid = db.ins("loan.loan_application", returning="application_id",
                       application_no=f"A-{args.seed}-{i:05d}", borrower_id=bid,
                       branch_id=rng.choice(branches), loan_officer_id=rng.choice(officers),
                       product_code=rng.choice([p[0] for p in PRODUCTS]),
                       channel_code=rng.choice([c[0] for c in CHANNELS]),
                       vehicle_id=rng.choice(vehicles), requested_amount=rng.randint(8000, 60000),
                       requested_term_months=rng.choice([36, 48, 60, 72]), status=status)
        if status in ("approved", "declined", "funded"):
            outcome = "approve" if status in ("approved", "funded") else "decline"
            did = db.ins("loan.underwriting_decision", returning="decision_id", application_id=appid,
                         underwriter_id=rng.choice(underwriters), decision=outcome,
                         approved_amount=rng.randint(8000, 60000), approved_apr=round(rng.uniform(3.5, 14.0), 3),
                         dti_ratio=round(rng.uniform(0.1, 0.55), 3), ltv_ratio=round(rng.uniform(0.6, 1.2), 3),
                         pd_score=round(rng.uniform(0.01, 0.4), 4), risk_rating=rng.choice([r[0] for r in RISK]))
            db.ins("loan.decision_reason_xref", decision_id=did,
                   reason_code=rng.choice([r[0] for r in REASONS]), weight=1.0)
            if status == "funded":
                lid = db.ins("loan.loan", returning="loan_id", application_id=appid,
                             principal=rng.randint(8000, 60000), apr=round(rng.uniform(3.5, 14.0), 3),
                             term_months=rng.choice([36, 48, 60, 72]), status="active")
                db.ins("loan.loan_terms", loan_id=lid, monthly_payment=rng.randint(180, 1100),
                       first_payment_date=fake.date_this_year())
                for m in range(rng.randint(6, 24)):
                    late = rng.random() < 0.08
                    db.ins("loan.payment", loan_id=lid, due_date=fake.date_this_decade(),
                           paid_date=fake.date_this_decade(), amount=rng.randint(180, 1100), is_late=late)

    # documents/pages from manifest -> link to example images
    man = os.path.join(args.example, "manifest.jsonl")
    docs = {}
    rows = [json.loads(ln) for ln in open(man, encoding="utf-8")] if os.path.exists(man) else []
    for r in rows:
        docs.setdefault(r["doc_id"], []).append(r)
    for doc_id, pages in docs.items():
        dc = pages[0]["doc_class"]
        documentid = db.ins("doc.document", returning="document_id", doc_class=dc,
                            doc_type_code=DOCCLASS_TO_TYPE[dc],
                            source_path=f"example/images/{pages[0]['image']}",
                            page_count=len(pages))
        for p in sorted(pages, key=lambda z: z["page_in_doc"]):
            db.ins("doc.document_page", document_id=documentid, page_no=p["page_in_doc"],
                   image_path=f"example/images/{p['image']}")

    # auth: roles + users (argon2 hashes)
    for code, label in [("admin", "Administrator"), ("underwriter", "Underwriter"),
                        ("officer", "Loan Officer"), ("auditor", "Auditor")]:
        db.ins("app.role", code=code, label=label)
    pwd_hash = _argon2("ChangeMe123!", args.dry_run)
    for uname, role in [("admin", "admin"), ("uw1", "underwriter"),
                        ("officer1", "officer"), ("auditor1", "auditor")]:
        db.ins("app.user_account", username=uname, email=f"{uname}@regloan.example",
               password_hash=pwd_hash, role=role, is_active=True)

    db.commit()
    total = sum(db.counts.values())
    print(f"{'DRY-RUN ' if args.dry_run else ''}seeded rows: {total} across {len(db.counts)} tables")
    print("documents:", db.counts.get("doc.document", 0),
          "pages:", db.counts.get("doc.document_page", 0),
          "applications:", db.counts.get("loan.loan_application", 0))


def _argon2(pwd, dry):
    if dry:
        return "$argon2id$v=19$m=65536,t=3,p=4$DRYRUNDRYRUN$placeholderhash"
    from argon2 import PasswordHasher
    return PasswordHasher().hash(pwd)


if __name__ == "__main__":
    main()
