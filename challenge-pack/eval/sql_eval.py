"""NL->SQL evaluation CLI.

Three metrics, matching the grading kit:

  * Execution Accuracy (EX) -- run gold and predicted SQL against ``DATABASE_URL``
    and compare the result rows as an *order-insensitive multiset*. This is the
    headline metric; it requires a reachable Postgres with the seeded schema.
  * Exact-Set-Match (ESM)   -- order-insensitive comparison of the *parsed* SQL
    clauses (SELECT items, tables, predicates) using sqlglot. Runs without a DB.
  * valid-SQL rate          -- fraction of predictions that parse under sqlglot's
    Postgres dialect. Runs without a DB.

Gold / prediction formats
-------------------------
``--gold`` JSON: a list of ``{"question": ..., "gold_sql": ...}`` objects, or a
mapping ``{question: gold_sql}``. Defaults to the public sample
``eval/sql_samples.json`` so the CLI is runnable out of the box; the private
grader points it at ``grading-kit/hidden/sql_gold.json``.

``--pred`` JSON: a mapping ``{question: predicted_sql}`` (your system's output).

Usage
-----
    # structure-only metrics (no DB needed)
    python -m eval.sql_eval --pred preds.json

    # full metrics incl. Execution Accuracy
    export DATABASE_URL=postgresql://app:app@localhost:5432/autoloan
    python -m eval.sql_eval --gold grading-kit/hidden/sql_gold.json --pred preds.json

Heavy deps (``sqlglot``, ``psycopg``) are imported lazily.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

DEFAULT_GOLD = os.path.join(os.path.dirname(__file__), "sql_samples.json")


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def load_gold(path: str) -> Dict[str, str]:
    with open(path, "r", encoding="utf-8") as fh:
        obj = json.load(fh)
    if isinstance(obj, dict):
        return {str(k): str(v) for k, v in obj.items()}
    out: Dict[str, str] = {}
    for row in obj:
        q = row.get("question")
        sql = row.get("gold_sql", row.get("sql"))
        if q and sql:
            out[str(q)] = str(sql)
    return out


def load_pred(path: str) -> Dict[str, str]:
    with open(path, "r", encoding="utf-8") as fh:
        obj = json.load(fh)
    if isinstance(obj, dict):
        return {str(k): str(v) for k, v in obj.items()}
    out: Dict[str, str] = {}
    for row in obj:
        q = row.get("question")
        sql = row.get("sql", row.get("pred_sql", row.get("predicted_sql")))
        if q and sql is not None:
            out[str(q)] = str(sql)
    return out


# --------------------------------------------------------------------------- #
# valid-SQL rate  (sqlglot parse under postgres dialect)
# --------------------------------------------------------------------------- #
def is_valid_sql(sql: str) -> bool:
    try:
        import sqlglot  # lazy / optional
    except Exception:  # pragma: no cover
        return False
    if not sql or not sql.strip():
        return False
    try:
        parsed = sqlglot.parse(sql, read="postgres")
        return bool(parsed) and all(stmt is not None for stmt in parsed)
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Exact-Set-Match  (structural, order-insensitive, via sqlglot)
# --------------------------------------------------------------------------- #
def _canonical_components(sql: str) -> Optional[Dict[str, frozenset]]:
    """Decompose a SELECT into order-insensitive clause component sets."""
    try:
        import sqlglot
        import sqlglot.expressions as exp
    except Exception:  # pragma: no cover
        return None
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except Exception:
        return None
    if tree is None:
        return None

    def norm(node) -> str:
        return node.sql(dialect="postgres", normalize=True, comments=False).lower()

    selects = frozenset(norm(e) for e in tree.find_all(exp.Alias, exp.Column, exp.Star) if e.parent_select is tree) \
        if tree.find(exp.Select) else frozenset()
    # Prefer the explicit projection list when available.
    sel = tree.find(exp.Select)
    if sel is not None:
        selects = frozenset(norm(e) for e in sel.expressions)
    tables = frozenset(norm(t) for t in tree.find_all(exp.Table))
    conditions = frozenset(
        norm(w.this) for w in tree.find_all(exp.Where) if w.this is not None
    )
    groups = frozenset(norm(g) for g in tree.find_all(exp.Group))
    havings = frozenset(norm(h.this) for h in tree.find_all(exp.Having) if h.this is not None)
    orders = frozenset(norm(o) for o in tree.find_all(exp.Order))
    return {
        "select": selects,
        "tables": tables,
        "where": conditions,
        "group": groups,
        "having": havings,
        "order": orders,
    }


def exact_set_match(pred_sql: str, gold_sql: str) -> bool:
    p = _canonical_components(pred_sql)
    g = _canonical_components(gold_sql)
    if p is None or g is None:
        return False
    return p == g


# --------------------------------------------------------------------------- #
# Execution Accuracy  (run against DATABASE_URL, compare result multisets)
# --------------------------------------------------------------------------- #
def _connect(database_url: str):
    """Open a DB connection. Tries psycopg (v3) then psycopg2. Lazy import."""
    try:
        import psycopg  # type: ignore
        return ("psycopg", psycopg.connect(database_url))
    except Exception:
        pass
    try:
        import psycopg2  # type: ignore
        return ("psycopg2", psycopg2.connect(database_url))
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "No working Postgres driver / connection. Install psycopg or psycopg2 "
            "and ensure DATABASE_URL is reachable. Underlying error: {}".format(exc)
        )


def _run_query(cur, sql: str) -> Tuple[bool, Optional[list]]:
    """Execute one query read-only. Returns ``(ok, rows_as_multiset_list)``."""
    try:
        cur.execute(sql)
        rows = cur.fetchall()
        # Normalise each row to a tuple of stringified cells so numeric/Decimal
        # vs int differences don't cause false negatives.
        normed = [tuple(_norm_cell(c) for c in row) for row in rows]
        return True, normed
    except Exception:
        return False, None


def _norm_cell(c) -> str:
    if c is None:
        return "\x00NULL"
    if isinstance(c, float):
        return "{:.6g}".format(c)
    try:
        from decimal import Decimal
        if isinstance(c, Decimal):
            return "{:.6g}".format(float(c))
    except Exception:
        pass
    return str(c)


def _multiset_equal(a: Optional[list], b: Optional[list]) -> bool:
    if a is None or b is None:
        return False
    from collections import Counter
    return Counter(a) == Counter(b)


def execution_accuracy(
    pairs: List[Tuple[str, str, str]], database_url: str
) -> Tuple[List[bool], List[str]]:
    """Run gold vs pred for each (question, gold, pred). Returns (matches, notes)."""
    kind, conn = _connect(database_url)
    matches: List[bool] = []
    notes: List[str] = []
    try:
        try:
            conn.autocommit = True
        except Exception:
            pass
        for _q, gold, pred in pairs:
            cur = conn.cursor()
            try:
                ok_g, rows_g = _run_query(cur, gold)
                # Roll back any aborted transaction before next stmt (psycopg2).
                if not ok_g:
                    _safe_rollback(conn)
                ok_p, rows_p = _run_query(cur, pred)
                if not ok_p:
                    _safe_rollback(conn)
            finally:
                cur.close()
            if not ok_g:
                matches.append(False)
                notes.append("gold failed to execute")
            elif not ok_p:
                matches.append(False)
                notes.append("pred failed to execute")
            else:
                eq = _multiset_equal(rows_g, rows_p)
                matches.append(eq)
                notes.append("match" if eq else "result mismatch")
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return matches, notes


def _safe_rollback(conn) -> None:
    try:
        conn.rollback()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def evaluate(gold: Dict[str, str], pred: Dict[str, str], database_url: Optional[str]) -> dict:
    questions = list(gold.keys())
    n = len(questions)

    valid_flags: List[bool] = []
    esm_flags: List[bool] = []
    have_pred: List[bool] = []
    exec_pairs: List[Tuple[str, str, str]] = []

    per_question: List[dict] = []

    for q in questions:
        g_sql = gold[q]
        p_sql = pred.get(q)
        has_pred = p_sql is not None
        have_pred.append(has_pred)
        valid = is_valid_sql(p_sql) if has_pred else False
        esm = exact_set_match(p_sql, g_sql) if has_pred else False
        valid_flags.append(valid)
        esm_flags.append(esm)
        if has_pred:
            exec_pairs.append((q, g_sql, p_sql))
        per_question.append(
            {"question": q, "has_pred": has_pred, "valid_sql": valid, "exact_set_match": esm}
        )

    summary: Dict[str, object] = {
        "n_questions": n,
        "n_predicted": sum(have_pred),
        "valid_sql_rate": round(sum(valid_flags) / n, 4) if n else None,
        "exact_set_match": round(sum(esm_flags) / n, 4) if n else None,
        "execution_accuracy": None,
    }

    if database_url:
        try:
            matches, notes = execution_accuracy(exec_pairs, database_url)
            # Map exec results back; unpredicted questions count as misses.
            ex_by_q = {exec_pairs[i][0]: matches[i] for i in range(len(exec_pairs))}
            note_by_q = {exec_pairs[i][0]: notes[i] for i in range(len(exec_pairs))}
            ex_hits = 0
            for row in per_question:
                q = row["question"]
                hit = bool(ex_by_q.get(q, False))
                row["execution_match"] = hit
                row["execution_note"] = note_by_q.get(q, "no prediction")
                ex_hits += 1 if hit else 0
            summary["execution_accuracy"] = round(ex_hits / n, 4) if n else None
        except Exception as exc:
            summary["execution_accuracy_error"] = str(exc)
    else:
        summary["_note"] = (
            "DATABASE_URL not set: Execution Accuracy skipped. "
            "Set DATABASE_URL to score EX (the headline metric)."
        )

    summary["per_question"] = per_question
    return summary


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eval.sql_eval",
        description="Score NL->SQL predictions (Execution Accuracy, Exact-Set-Match, valid-SQL).",
    )
    p.add_argument(
        "--gold",
        default=DEFAULT_GOLD,
        help="Gold JSON (default: eval/sql_samples.json public sample).",
    )
    p.add_argument("--pred", required=True, help="Prediction JSON: {question: sql}.")
    p.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres URL for Execution Accuracy (default: $DATABASE_URL).",
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not os.path.exists(args.gold):
        print(json.dumps({"error": "gold file not found: " + args.gold}))
        return 2
    if not os.path.exists(args.pred):
        print(json.dumps({"error": "pred file not found: " + args.pred}))
        return 2
    gold = load_gold(args.gold)
    pred = load_pred(args.pred)
    summary = evaluate(gold, pred, args.database_url)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
