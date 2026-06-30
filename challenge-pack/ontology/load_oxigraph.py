#!/usr/bin/env python3
"""Load the ontology + instance TTL(s) into an in-memory pyoxigraph store and
run SPARQL queries against it.

Key entry points:
  * ``load_store(paths)``      -> pyoxigraph.Store with the given TTL files loaded.
  * ``query(sparql, store=..)`` -> list[dict] rows (variable name -> string value),
                                   loading the default TTLs on first use if no
                                   store is provided.
  * CLI / ``__main__``         -> loads auto_loan.ttl (+ instances.ttl if present)
                                   and runs a sample transitive-supersedes query.

pyoxigraph is imported lazily so this module imports without the dependency.

Usage:
  python load_oxigraph.py                       # run the built-in sample query
  python load_oxigraph.py --ttl auto_loan.ttl --ttl instances.ttl \
      --query "SELECT ?r WHERE { ?r a <...#PolicyRule> }"
  python load_oxigraph.py --samples sparql_samples.json   # run all public samples
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Iterable

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TTLS = ["auto_loan.ttl", "instances.ttl"]

# A self-contained sample: rules transitively superseded by LTV-2023 (the head
# of the 3-level chain in auto_loan.ttl). Expected: LTV-2021 and LTV-2019.
SAMPLE_SPARQL = """
PREFIX al: <https://autoloan-docintel.example/ontology#>
SELECT ?superseded ?code WHERE {
  ?head al:ruleCode "LTV-2023" .
  ?head al:supersedes ?superseded .
  ?superseded al:ruleCode ?code .
}
ORDER BY ?code
"""


# --------------------------------------------------------------------------- #
#  Store loading
# --------------------------------------------------------------------------- #
def _resolve(paths: Iterable[str]) -> list[str]:
    """Resolve TTL paths relative to this file; keep only existing files."""
    out = []
    for p in paths:
        cand = p if os.path.isabs(p) else os.path.join(HERE, p)
        if os.path.exists(cand):
            out.append(cand)
    return out


def load_store(paths: Iterable[str] | None = None):
    """Return a pyoxigraph.Store populated from the given TTL files.

    With OWL transitive reasoning NOT performed natively by oxigraph, queries
    that need transitive ``al:supersedes`` should use SPARQL property paths
    (``al:supersedes+``) — see sparql_samples.json.
    """
    import pyoxigraph  # lazy

    ttls = _resolve(paths or DEFAULT_TTLS)
    if not ttls:
        raise FileNotFoundError(
            f"No TTL files found among {list(paths or DEFAULT_TTLS)} (cwd={HERE})"
        )

    store = pyoxigraph.Store()
    for ttl in ttls:
        with open(ttl, "rb") as fh:
            # pyoxigraph>=0.4 uses RdfFormat; older uses a mime string.
            try:
                from pyoxigraph import RdfFormat

                store.load(fh, format=RdfFormat.TURTLE)
            except Exception:
                store.load(fh, "text/turtle")
    return store


# --------------------------------------------------------------------------- #
#  Querying
# --------------------------------------------------------------------------- #
def _value(term) -> Any:
    """Extract a plain Python value from a pyoxigraph term."""
    # NamedNode/Literal/BlankNode all expose .value in pyoxigraph.
    return getattr(term, "value", str(term))


def query(sparql: str, store=None, paths: Iterable[str] | None = None) -> list[dict]:
    """Run a SELECT/ASK query and return rows as a list of dicts.

    For SELECT: each row maps variable name -> string value (None for unbound).
    For ASK:    returns ``[{"result": True/False}]``.
    """
    if store is None:
        store = load_store(paths)

    results = store.query(sparql)

    # ASK queries return a bool-like QueryBoolean in pyoxigraph.
    if isinstance(results, bool):
        return [{"result": results}]
    if hasattr(results, "__bool__") and not hasattr(results, "__iter__"):
        return [{"result": bool(results)}]

    rows: list[dict] = []
    variables = [str(v).lstrip("?") for v in getattr(results, "variables", [])]
    for solution in results:
        if variables:
            row = {}
            for var in variables:
                term = solution[var]
                row[var] = _value(term) if term is not None else None
            rows.append(row)
        else:
            # Fallback: iterate positionally.
            rows.append({"value": _value(solution)})
    return rows


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def _run_samples(store, samples_path: str) -> int:
    with open(samples_path, "r", encoding="utf-8") as fh:
        samples = json.load(fh)
    for i, item in enumerate(samples, 1):
        rows = query(item["sparql"], store=store)
        print(f"[{i}] {item.get('nl','')}")
        print(f"    rows={len(rows)}  sample={rows[:3]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Load TTL into pyoxigraph and query it.")
    ap.add_argument(
        "--ttl",
        action="append",
        default=None,
        help="TTL file(s) to load (repeatable). Default: auto_loan.ttl + instances.ttl",
    )
    ap.add_argument("--query", help="a SPARQL query to run (overrides the built-in sample)")
    ap.add_argument("--samples", help="path to a sparql_samples.json to run all queries")
    args = ap.parse_args(argv)

    try:
        store = load_store(args.ttl)
    except ImportError:
        print("ERROR: pyoxigraph is not installed (pip install pyoxigraph)", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    if args.samples:
        return _run_samples(store, args.samples)

    sparql = args.query or SAMPLE_SPARQL
    rows = query(sparql, store=store)
    print(json.dumps(rows, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
