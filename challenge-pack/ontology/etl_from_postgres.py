#!/usr/bin/env python3
"""ETL: project the auto-loan warehouse into RDF instances (ABox).

Reads the `loan` / `ref` tables over psycopg (DATABASE_URL) and emits an
``instances.ttl`` file using rdflib, conforming to the vocabulary defined in
``auto_loan.ttl`` (prefix ``al:``). The emitted graph is loadable alongside the
TBox by ``load_oxigraph.py`` and answerable by the SPARQL samples.

Design notes:
  * Heavy/optional deps (psycopg, rdflib) are imported lazily inside functions
    so this module imports cleanly without them installed.
  * IRIs are minted deterministically from primary keys, e.g.
    ``al:app-<application_id>`` so re-runs are stable and joinable.
  * The mapping mirrors the relational FK graph:
        loan_application --submittedBy--> borrower
        loan_application --hasCollateral--> vehicle
        loan_application --governedBy--> policy_rule (via underwriting_decision.policy_doc_id
                                          OR loan_application.branch -> region -> policy_rule)
        loan_application --resultsIn--> underwriting_decision
        policy_rule      --supersedes--> policy_rule (self-FK -> transitive chain)
        policy_rule      --appliesToRegion--> region

Usage:
  DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres \
    python etl_from_postgres.py --out instances.ttl
  python etl_from_postgres.py --limit 500 --out instances.ttl
"""
from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
from typing import Any

AL = "https://autoloan-docintel.example/ontology#"


# --------------------------------------------------------------------------- #
#  Graph construction helpers
# --------------------------------------------------------------------------- #
def _new_graph():
    """Create an rdflib Graph with the al: prefix bound (lazy import)."""
    from rdflib import Graph, Namespace

    g = Graph()
    g.bind("al", Namespace(AL))
    return g


def _iri(local: str):
    from rdflib import URIRef

    return URIRef(AL + local)


def _term(value: Any):
    """Coerce a Python/DB value into an rdflib term (Literal with typed datatype)."""
    from rdflib import Literal
    from rdflib.namespace import XSD

    if isinstance(value, bool):
        return Literal(value, datatype=XSD.boolean)
    if isinstance(value, Decimal):
        return Literal(str(value), datatype=XSD.decimal)
    if isinstance(value, int):
        return Literal(value, datatype=XSD.integer)
    if isinstance(value, float):
        return Literal(value, datatype=XSD.decimal)
    return Literal(str(value))


def _add(g, subj, pred_local: str, obj):
    from rdflib import RDF, RDFS, URIRef

    if pred_local == "a":
        g.add((subj, RDF.type, obj))
    elif pred_local == "label":
        g.add((subj, RDFS.label, _term(obj)))
    elif isinstance(obj, URIRef):
        g.add((subj, _iri(pred_local), obj))
    else:
        g.add((subj, _iri(pred_local), _term(obj)))


# --------------------------------------------------------------------------- #
#  DB access
# --------------------------------------------------------------------------- #
def _connect(dsn: str):
    import psycopg  # lazy

    return psycopg.connect(dsn)


def _rows(conn, sql: str, params: tuple = ()) -> list[dict]:
    """Run a query and return rows as dicts (column name -> value)."""
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


# --------------------------------------------------------------------------- #
#  Mappers (relational rows -> RDF)
# --------------------------------------------------------------------------- #
def map_regions(g, conn) -> None:
    for r in _rows(conn, "SELECT region_id, name FROM ref.region"):
        s = _iri(f"region-{r['region_id']}")
        _add(g, s, "a", _iri("Region"))
        _add(g, s, "regionName", r["name"])
        _add(g, s, "label", r["name"])


def map_products(g, conn) -> None:
    for r in _rows(conn, "SELECT code, label FROM ref.loan_product"):
        s = _iri(f"product-{r['code']}")
        _add(g, s, "a", _iri("LoanProduct"))
        _add(g, s, "label", r["label"])


def map_policy_rules(g, conn) -> None:
    sql = """
        SELECT policy_rule_id, rule_code, description,
               supersedes_rule_id, region_id
        FROM loan.policy_rule
    """
    for r in _rows(conn, sql):
        s = _iri(f"rule-{r['policy_rule_id']}")
        _add(g, s, "a", _iri("PolicyRule"))
        _add(g, s, "ruleCode", r["rule_code"])
        if r.get("description"):
            _add(g, s, "label", r["description"])
        if r.get("supersedes_rule_id") is not None:
            _add(g, s, "supersedes", _iri(f"rule-{r['supersedes_rule_id']}"))
        if r.get("region_id") is not None:
            _add(g, s, "appliesToRegion", _iri(f"region-{r['region_id']}"))


def map_credit_profiles(g, conn) -> None:
    # One CreditProfile per borrower, carrying the credit_band as ficoBand.
    sql = "SELECT borrower_id, credit_band FROM loan.borrower WHERE credit_band IS NOT NULL"
    for r in _rows(conn, sql):
        s = _iri(f"credit-{r['borrower_id']}")
        _add(g, s, "a", _iri("CreditProfile"))
        _add(g, s, "ficoBand", r["credit_band"])


def map_income_sources(g, conn, limit: int | None) -> None:
    sql = (
        "SELECT income_source_id, borrower_id, income_type, monthly_amount "
        "FROM loan.income_source"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    for r in _rows(conn, sql):
        s = _iri(f"income-{r['income_source_id']}")
        _add(g, s, "a", _iri("IncomeSource"))
        if r.get("income_type"):
            _add(g, s, "label", r["income_type"])
        b = _iri(f"borrower-{r['borrower_id']}")
        _add(g, b, "hasIncomeSource", s)


def map_borrowers(g, conn, limit: int | None) -> None:
    sql = """
        SELECT b.borrower_id, b.credit_band,
               p.first_name, p.last_name, p.legal_name
        FROM loan.borrower b
        JOIN loan.party p ON p.party_id = b.party_id
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    for r in _rows(conn, sql):
        s = _iri(f"borrower-{r['borrower_id']}")
        _add(g, s, "a", _iri("Borrower"))
        name = (
            f"{r.get('first_name') or ''} {r.get('last_name') or ''}".strip()
            or r.get("legal_name")
            or f"borrower-{r['borrower_id']}"
        )
        _add(g, s, "label", name)
        if r.get("credit_band"):
            _add(g, s, "hasCreditProfile", _iri(f"credit-{r['borrower_id']}"))


def map_vehicles(g, conn, limit: int | None) -> None:
    # vehicleAge approximated from model_year vs current year.
    sql = """
        SELECT vehicle_id, vin, model_year, condition,
               EXTRACT(YEAR FROM now())::int - COALESCE(model_year, EXTRACT(YEAR FROM now())::int) AS age
        FROM loan.vehicle
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    for r in _rows(conn, sql):
        s = _iri(f"vehicle-{r['vehicle_id']}")
        _add(g, s, "a", _iri("Vehicle"))
        if r.get("vin"):
            _add(g, s, "label", r["vin"])
        age = r.get("age")
        if age is not None:
            _add(g, s, "vehicleAge", int(age))


def map_decisions(g, conn, limit: int | None) -> None:
    sql = """
        SELECT decision_id, application_id, decision,
               dti_ratio, ltv_ratio, policy_doc_id
        FROM loan.underwriting_decision
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    for r in _rows(conn, sql):
        s = _iri(f"decision-{r['decision_id']}")
        _add(g, s, "a", _iri("Decision"))
        if r.get("decision"):
            _add(g, s, "decisionOutcome", r["decision"])
        if r.get("ltv_ratio") is not None:
            _add(g, s, "ltv", r["ltv_ratio"])
        if r.get("dti_ratio") is not None:
            _add(g, s, "dti", r["dti_ratio"])
        # application --resultsIn--> decision
        if r.get("application_id") is not None:
            a = _iri(f"app-{r['application_id']}")
            _add(g, a, "resultsIn", s)


def map_applications(g, conn, limit: int | None) -> None:
    sql = """
        SELECT a.application_id, a.application_no, a.borrower_id,
               a.product_code, a.vehicle_id, a.requested_amount,
               a.requested_term_months, br.region_id AS branch_region_id
        FROM loan.loan_application a
        LEFT JOIN loan.branch br ON br.branch_id = a.branch_id
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    for r in _rows(conn, sql):
        s = _iri(f"app-{r['application_id']}")
        _add(g, s, "a", _iri("Application"))
        _add(g, s, "label", r["application_no"])
        if r.get("borrower_id") is not None:
            _add(g, s, "submittedBy", _iri(f"borrower-{r['borrower_id']}"))
        if r.get("vehicle_id") is not None:
            _add(g, s, "hasCollateral", _iri(f"vehicle-{r['vehicle_id']}"))
        if r.get("product_code"):
            _add(g, s, "usesProduct", _iri(f"product-{r['product_code']}"))
        if r.get("requested_amount") is not None:
            _add(g, s, "loanAmount", r["requested_amount"])
        if r.get("requested_term_months") is not None:
            _add(g, s, "term", int(r["requested_term_months"]))
        if r.get("branch_region_id") is not None:
            _add(g, s, "inRegion", _iri(f"region-{r['branch_region_id']}"))


def map_app_governed_by(g, conn, limit: int | None) -> None:
    """Link applications to the policy rules of their branch region.

    Approximation: an application is governedBy every policy_rule whose region
    matches the application's branch region. This makes region/policy multi-hop
    queries answerable. (A production ETL would resolve the specific rule from
    underwriting_decision.policy_doc_id -> policy_document -> policy_rule.)
    """
    sql = """
        SELECT a.application_id, pr.policy_rule_id
        FROM loan.loan_application a
        JOIN loan.branch br ON br.branch_id = a.branch_id
        JOIN loan.policy_rule pr ON pr.region_id = br.region_id
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    for r in _rows(conn, sql):
        a = _iri(f"app-{r['application_id']}")
        _add(g, a, "governedBy", _iri(f"rule-{r['policy_rule_id']}"))


# --------------------------------------------------------------------------- #
#  Orchestration
# --------------------------------------------------------------------------- #
def build_graph(dsn: str, limit: int | None = None):
    """Connect to Postgres and return a populated rdflib Graph."""
    g = _new_graph()
    conn = _connect(dsn)
    try:
        map_regions(g, conn)
        map_products(g, conn)
        map_policy_rules(g, conn)
        map_credit_profiles(g, conn)
        map_borrowers(g, conn, limit)
        map_income_sources(g, conn, limit)
        map_vehicles(g, conn, limit)
        map_applications(g, conn, limit)
        map_decisions(g, conn, limit)
        map_app_governed_by(g, conn, limit)
    finally:
        conn.close()
    return g


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Emit instances.ttl from the loan warehouse.")
    ap.add_argument(
        "--out",
        default="instances.ttl",
        help="output Turtle path (default: instances.ttl)",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="optional per-table row cap (smoke tests)",
    )
    ap.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres DSN (default: $DATABASE_URL)",
    )
    args = ap.parse_args(argv)

    if not args.database_url:
        print("ERROR: set DATABASE_URL or pass --database-url", file=sys.stderr)
        return 2

    g = build_graph(args.database_url, limit=args.limit)
    g.serialize(destination=args.out, format="turtle")
    print(f"wrote {len(g)} triples -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
