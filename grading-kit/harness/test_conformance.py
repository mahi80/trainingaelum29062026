"""Structural conformance checks for an AutoLoan-DocIntel submission.

These are the *auto-gradable* invariants that do not need a running app:

  * the DDL declares >= 100 tables and >= 200 columns
  * the required tables / columns / FKs from the brief are present
    (if DATABASE_URL is set we query information_schema on the live DB;
     otherwise we parse the .sql with sqlglot, falling back to regex)
  * example/manifest.jsonl integrity (100 rows, valid splits, required keys)
  * SHA256SUMS verifies for the public example files
  * leakage: TEST-page answer GT is ABSENT from challenge-pack/example/gt
    but PRESENT in grading-kit/hidden/gt_test

Run:  pytest grading-kit/harness/test_conformance.py -v
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import pytest

from conftest import (
    DDL_DIR,
    EXAMPLE_DIR,
    GT_DIR,
    HIDDEN_GT_DIR,
    iter_ddl_sql,
)

# --------------------------------------------------------------------------- #
# constants from the brief
# --------------------------------------------------------------------------- #
MIN_TABLES = 100
MIN_COLUMNS = 200
MANIFEST_ROWS = 100
VALID_SPLITS = {"train", "test"}
MANIFEST_REQUIRED_KEYS = {
    "page_no", "page", "image", "doc_id", "doc_class",
    "page_in_doc", "table_uid", "distortions", "has_handwriting", "split",
}

# table -> columns that MUST exist (subset; from the brief's schema summary)
REQUIRED_TABLES: dict[str, set[str]] = {
    "loan.loan_application": {
        "application_id", "application_no", "borrower_id", "branch_id",
        "product_code", "vehicle_id", "requested_amount",
        "requested_term_months", "status", "submitted_at", "decided_at",
    },
    "loan.borrower": {"borrower_id", "party_id", "credit_band"},
    "loan.party": {"party_id", "first_name", "last_name", "dob", "ssn_tokenized"},
    "loan.vehicle": {"vehicle_id", "vin", "make_id", "fuel_type", "condition"},
    "loan.underwriting_decision": {
        "decision_id", "application_id", "decision",
        "dti_ratio", "ltv_ratio", "pd_score", "risk_rating",
    },
    "loan.policy_rule": {"policy_rule_id", "rule_code", "supersedes_rule_id", "region_id"},
    "loan.branch": {"branch_id"},
    "doc.document": {"document_id"},
    "doc.document_page": {"page_id", "document_id", "page_no", "image_path"},
    "doc.document_chunk": {"chunk_id", "page_id", "chunk_type", "content", "embedding"},
    "app.user_account": {"user_id", "username", "email", "password_hash", "role", "is_active"},
    "app.role": {"code", "label"},
    "app.session": {"session_id", "user_id", "created_at", "last_seen_at", "expires_at"},
    "app.schema_embedding": set(),
}

# (child_table, child_col) -> parent_table  ; FKs that MUST exist
REQUIRED_FKS: dict[tuple[str, str], str] = {
    ("loan.loan_application", "borrower_id"): "loan.borrower",
    ("loan.loan_application", "vehicle_id"): "loan.vehicle",
    ("loan.borrower", "party_id"): "loan.party",
    ("loan.underwriting_decision", "application_id"): "loan.loan_application",
    ("doc.document_page", "document_id"): "doc.document",
    ("doc.document_chunk", "page_id"): "doc.document_page",
    ("app.session", "user_id"): "app.user_account",
}


# --------------------------------------------------------------------------- #
# SQL parsing: sqlglot first, regex fallback
# --------------------------------------------------------------------------- #
def _norm(name: str) -> str:
    """Normalise a (possibly schema-qualified, possibly quoted) identifier."""
    return name.replace('"', "").strip().lower()


def _parse_with_sqlglot(sql: str):
    """Return (tables:set[str], columns_by_table:dict[str,set[str]], fks:set[(t,c,parent)])
    or None if sqlglot is unavailable / cannot parse anything."""
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        return None

    tables: set[str] = set()
    cols: dict[str, set[str]] = {}
    fks: set[tuple[str, str, str]] = set()

    try:
        statements = sqlglot.parse(sql, read="postgres")
    except Exception:
        return None

    parsed_any = False
    for stmt in statements:
        if stmt is None:
            continue
        create = stmt if isinstance(stmt, exp.Create) else stmt.find(exp.Create)
        if create is None or (create.args.get("kind") or "").upper() != "TABLE":
            continue
        schema_node = create.this  # exp.Schema wrapping the table + column defs
        table_node = schema_node.this if isinstance(schema_node, exp.Schema) else schema_node
        if not isinstance(table_node, exp.Table):
            continue
        parsed_any = True
        db = _norm(table_node.db) if table_node.db else ""
        tname = _norm(table_node.name)
        full = f"{db}.{tname}" if db else tname
        tables.add(full)
        cols.setdefault(full, set())

        if isinstance(schema_node, exp.Schema):
            for col_def in schema_node.expressions:
                if isinstance(col_def, exp.ColumnDef):
                    cols[full].add(_norm(col_def.name))
                    # inline REFERENCES on a column
                    for ref in col_def.find_all(exp.Reference):
                        parent = ref.find(exp.Table)
                        if parent is not None:
                            pdb = _norm(parent.db) if parent.db else db
                            pfull = f"{pdb}.{_norm(parent.name)}" if pdb else _norm(parent.name)
                            fks.add((full, _norm(col_def.name), pfull))
                elif isinstance(col_def, exp.ForeignKey):
                    fk_cols = [_norm(c.name) for c in col_def.find_all(exp.Identifier)]
                    ref = col_def.find(exp.Reference)
                    parent = ref.find(exp.Table) if ref is not None else None
                    if parent is not None and fk_cols:
                        pdb = _norm(parent.db) if parent.db else db
                        pfull = f"{pdb}.{_norm(parent.name)}" if pdb else _norm(parent.name)
                        fks.add((full, fk_cols[0], pfull))

    if not parsed_any:
        return None
    return tables, cols, fks


_CREATE_RE = re.compile(
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?([a-z0-9_\".]+)\s*\((.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)
_REF_RE = re.compile(
    r"references\s+([a-z0-9_\".]+)\s*\(\s*([a-z0-9_\"]+)\s*\)",
    re.IGNORECASE,
)


def _split_top_level(body: str) -> list[str]:
    """Split a CREATE TABLE body on top-level commas (ignoring nested parens)."""
    out, depth, cur = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


_CONSTRAINT_LEADS = (
    "primary", "foreign", "unique", "check", "constraint", "exclude", "like",
)


def _parse_with_regex(sql: str):
    tables: set[str] = set()
    cols: dict[str, set[str]] = {}
    fks: set[tuple[str, str, str]] = set()

    for m in _CREATE_RE.finditer(sql):
        full = _norm(m.group(1))
        body = m.group(2)
        tables.add(full)
        cols.setdefault(full, set())
        for piece in _split_top_level(body):
            piece = piece.strip()
            if not piece:
                continue
            first = piece.split(None, 1)[0].lower().strip('"')
            if first in _CONSTRAINT_LEADS:
                # table-level constraint; still capture FK target if any
                fk = _REF_RE.search(piece)
                fcols = re.search(r"foreign\s+key\s*\(\s*([a-z0-9_\"]+)", piece, re.IGNORECASE)
                if fk and fcols:
                    fks.add((full, _norm(fcols.group(1)), _norm(fk.group(1))))
                continue
            colname = _norm(first)
            cols[full].add(colname)
            fk = _REF_RE.search(piece)
            if fk:
                fks.add((full, colname, _norm(fk.group(1))))
    return tables, cols, fks


def _parse_ddl(sql: str):
    """Best-effort schema extraction. Prefer sqlglot; regex fallback guarantees a result."""
    return _parse_with_sqlglot(sql) or _parse_with_regex(sql)


# --------------------------------------------------------------------------- #
# information_schema introspection (when DATABASE_URL is set)
# --------------------------------------------------------------------------- #
def _introspect_db(database_url: str):
    import psycopg

    with psycopg.connect(database_url, connect_timeout=5) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_schema || '.' || table_name, column_name
            FROM information_schema.columns
            WHERE table_schema NOT IN ('pg_catalog','information_schema')
            """
        )
        cols: dict[str, set[str]] = {}
        for tbl, col in cur.fetchall():
            cols.setdefault(_norm(tbl), set()).add(_norm(col))
        tables = set(cols)

        cur.execute(
            """
            SELECT tc.table_schema || '.' || tc.table_name   AS child,
                   kcu.column_name                            AS child_col,
                   ccu.table_schema || '.' || ccu.table_name AS parent
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema   = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name
             AND tc.table_schema   = ccu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
            """
        )
        fks = {(_norm(c), _norm(cc), _norm(p)) for c, cc, p in cur.fetchall()}
    return tables, cols, fks


@pytest.fixture(scope="session")
def schema_facts():
    """(tables, columns_by_table, fks). Live DB if DATABASE_URL else parsed DDL."""
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        try:
            import psycopg  # noqa: F401
            return _introspect_db(database_url)
        except Exception:
            pass  # fall through to static parsing
    assert DDL_DIR.is_dir(), f"DDL dir missing: {DDL_DIR}"
    return _parse_ddl(iter_ddl_sql())


# --------------------------------------------------------------------------- #
# tests: schema scale
# --------------------------------------------------------------------------- #
def test_ddl_directory_exists():
    assert DDL_DIR.is_dir(), f"missing DDL directory {DDL_DIR}"
    assert list(DDL_DIR.glob("*.sql")), "no .sql files in db/ddl"


def test_at_least_100_tables(schema_facts):
    tables, _cols, _fks = schema_facts
    assert len(tables) >= MIN_TABLES, f"only {len(tables)} tables (need >= {MIN_TABLES})"


def test_at_least_200_columns(schema_facts):
    _tables, cols, _fks = schema_facts
    total = sum(len(c) for c in cols.values())
    assert total >= MIN_COLUMNS, f"only {total} columns (need >= {MIN_COLUMNS})"


# --------------------------------------------------------------------------- #
# tests: required tables / columns / FKs
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("table", sorted(REQUIRED_TABLES))
def test_required_table_present(schema_facts, table):
    tables, _cols, _fks = schema_facts
    assert table in tables, f"required table missing: {table}"


@pytest.mark.parametrize(
    "table,column",
    sorted((t, c) for t, cs in REQUIRED_TABLES.items() for c in cs),
)
def test_required_column_present(schema_facts, table, column):
    _tables, cols, _fks = schema_facts
    have = cols.get(table, set())
    assert column in have, f"{table} missing required column '{column}' (have: {sorted(have)})"


@pytest.mark.parametrize(
    "child,child_col,parent",
    sorted((c, cc, p) for (c, cc), p in REQUIRED_FKS.items()),
)
def test_required_fk_present(schema_facts, child, child_col, parent):
    _tables, _cols, fks = schema_facts
    assert (child, child_col, parent) in fks, (
        f"required FK missing: {child}.{child_col} -> {parent}"
    )


# --------------------------------------------------------------------------- #
# tests: manifest integrity
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def manifest_rows():
    path = EXAMPLE_DIR / "manifest.jsonl"
    assert path.is_file(), f"missing manifest: {path}"
    rows = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            pytest.fail(f"manifest line {i} is not valid JSON: {exc}")
    return rows


def test_manifest_row_count(manifest_rows):
    assert len(manifest_rows) == MANIFEST_ROWS, (
        f"manifest has {len(manifest_rows)} rows (expected {MANIFEST_ROWS})"
    )


def test_manifest_required_keys(manifest_rows):
    for i, row in enumerate(manifest_rows, 1):
        missing = MANIFEST_REQUIRED_KEYS - set(row)
        assert not missing, f"manifest row {i} missing keys: {sorted(missing)}"


def test_manifest_splits_valid_and_both_present(manifest_rows):
    splits = {r["split"] for r in manifest_rows}
    assert splits <= VALID_SPLITS, f"unexpected split values: {splits - VALID_SPLITS}"
    assert VALID_SPLITS <= splits, f"missing a split; saw {splits}"
    n_test = sum(1 for r in manifest_rows if r["split"] == "test")
    assert n_test > 0, "no test-split pages in manifest"


def test_manifest_pages_unique(manifest_rows):
    pages = [r["page"] for r in manifest_rows]
    assert len(pages) == len(set(pages)), "duplicate page ids in manifest"


# --------------------------------------------------------------------------- #
# tests: SHA256SUMS verifies for public example files
# --------------------------------------------------------------------------- #
def _parse_sha256sums(path: Path) -> list[tuple[str, str]]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.rstrip("\n")
        if not line.strip():
            continue
        # format:  <hex>  <relpath>   (two spaces; '*' binary marker tolerated)
        parts = re.split(r"\s+", line.strip(), maxsplit=1)
        if len(parts) != 2:
            continue
        digest, rel = parts
        out.append((digest.lower(), rel.lstrip("*").strip()))
    return out


def test_sha256sums_present():
    assert (EXAMPLE_DIR / "SHA256SUMS").is_file(), "missing example/SHA256SUMS"


def test_sha256sums_verify(manifest_rows):  # noqa: ARG001 (forces example dir presence)
    sums_file = EXAMPLE_DIR / "SHA256SUMS"
    if not sums_file.is_file():
        pytest.skip("no SHA256SUMS to verify")
    entries = _parse_sha256sums(sums_file)
    assert entries, "SHA256SUMS parsed to zero entries"
    mismatches: list[str] = []
    missing: list[str] = []
    for digest, rel in entries:
        target = EXAMPLE_DIR / rel
        if not target.is_file():
            missing.append(rel)
            continue
        h = hashlib.sha256()
        with target.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        if h.hexdigest() != digest:
            mismatches.append(rel)
    assert not missing, f"SHA256SUMS lists {len(missing)} missing files, e.g. {missing[:5]}"
    assert not mismatches, f"{len(mismatches)} files failed checksum, e.g. {mismatches[:5]}"


# --------------------------------------------------------------------------- #
# tests: leakage — test-page answer GT must NOT be in the public pack
# --------------------------------------------------------------------------- #
# answer formats that constitute "the key" (meta.json is provided for all pages,
# so it is explicitly excluded from the leakage check).
ANSWER_SUFFIXES = (".cells.json", ".tables.html", ".hocr", ".alto.xml")


def _test_page_ids(manifest_rows) -> set[str]:
    return {r["page"] for r in manifest_rows if r["split"] == "test"}


def test_test_pages_exist():
    assert any(r["split"] == "test" for r in _load_manifest_rows()), "no test pages"


def _load_manifest_rows():
    path = EXAMPLE_DIR / "manifest.jsonl"
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_no_test_gt_leakage_in_public_pack(manifest_rows):
    """For every TEST page, none of its answer-GT files may exist under example/gt."""
    leaked: list[str] = []
    for page in _test_page_ids(manifest_rows):
        for suffix in ANSWER_SUFFIXES:
            if (GT_DIR / f"{page}{suffix}").exists():
                leaked.append(f"{page}{suffix}")
    assert not leaked, f"LEAKAGE: test-page answer GT present in public pack: {leaked}"


def test_hidden_gt_present_for_test_pages(manifest_rows):
    """The withheld answers must live in grading-kit/hidden/gt_test for each test page."""
    if not HIDDEN_GT_DIR.is_dir():
        pytest.skip(f"hidden GT dir absent: {HIDDEN_GT_DIR}")
    test_pages = sorted(_test_page_ids(manifest_rows))
    missing: list[str] = []
    for page in test_pages:
        # require at least the cells.json key for each test page
        if not (HIDDEN_GT_DIR / f"{page}.cells.json").exists():
            missing.append(f"{page}.cells.json")
    assert not missing, f"hidden GT incomplete; missing keys: {missing[:10]}"
