# Ontology + SPARQL track

This module models the auto-loan domain as an OWL/RDFS ontology and lets you
answer **graph-shaped** questions that are awkward in SQL — transitive policy
supersession, multi-hop region/policy reachability, and provenance of decisions.

## Files

| File | Purpose |
|------|---------|
| `auto_loan.ttl` | TBox (classes, object/datatype properties) **plus** a small hand-authored ABox used by the public/hidden queries. Prefix `al:` = `https://autoloan-docintel.example/ontology#`. |
| `etl_from_postgres.py` | Reads the `loan`/`ref` warehouse over `psycopg` (`DATABASE_URL`) and emits `instances.ttl` via `rdflib`, conforming to the `al:` vocabulary. |
| `load_oxigraph.py` | Loads TTL(s) into an in-memory `pyoxigraph` store; exposes `query(sparql) -> rows` and a `__main__` sample query. |
| `sparql_samples.json` | 8 public `{nl, sparql}` pairs requiring graph reasoning. |
| `requirements.txt` | `rdflib`, `pyoxigraph`, `psycopg`. |

> The hidden gold set (`grading-kit/hidden/sparql_gold.json`) adds 15
> `{nl, sparql, expected_count}` pairs evaluated against the same ABox.

## Vocabulary (TBox)

**Classes:** `Borrower` (`CoBorrower` ⊑ `Borrower`), `Application`,
`Collateral` (`Vehicle` ⊑ `Collateral`), `LoanProduct`, `Decision`,
`PolicyRule`, `Region`, `IncomeSource`, `CreditProfile`.

**Object properties:** `hasCollateral`, `submittedBy`, `governedBy`,
`supersedes` (**`owl:TransitiveProperty`**), `appliesToRegion`, `resultsIn`,
`derivedFrom`, plus helpers `usesProduct`, `inRegion`, `hasIncomeSource`,
`hasCreditProfile`.

**Datatype properties:** `ltv`, `dti`, `ficoBand`, `loanAmount`, `term`,
`vehicleAge`, `ruleCode`, `decisionOutcome`, `regionName`.

### Supersession chain

`auto_loan.ttl` contains a 3-level chain so transitive queries are exercised:

```
LTV-2023  --supersedes-->  LTV-2021  --supersedes-->  LTV-2019
```

plus a 2-level chain `DTI-2022 --supersedes--> DTI-2020`. By transitivity
`LTV-2023` supersedes both `LTV-2021` and `LTV-2019`.

## Quick start

```bash
pip install -r requirements.txt

# 1) Run the built-in transitive-supersedes sample against the bundled ABox
python load_oxigraph.py

# 2) Run all 8 public samples
python load_oxigraph.py --samples sparql_samples.json

# 3) Run one ad-hoc query
python load_oxigraph.py \
  --query 'PREFIX al: <https://autoloan-docintel.example/ontology#>
           SELECT (COUNT(*) AS ?n) WHERE { ?r a al:PolicyRule }'
```

### Generating instances from the real DB

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres \
  python etl_from_postgres.py --out instances.ttl

# load_oxigraph picks up instances.ttl automatically if present
python load_oxigraph.py
```

## Reasoning note (oxigraph vs OWL)

`oxigraph` does **not** materialise OWL semantics, so `owl:TransitiveProperty`
alone will not expand `al:supersedes`. Use a **SPARQL property path** instead:

- `al:supersedes+`  — one-or-more hops (strict ancestors)
- `al:supersedes*`  — zero-or-more hops (includes the rule itself)

The public and hidden queries are written with property paths so they return
the full transitive closure without a separate reasoner. If you load the graph
into an OWL reasoner (e.g. `owlrl` over `rdflib`), the materialised triples make
plain `al:supersedes` return the same closure.

## What candidates implement

The provided code is fully working. Candidate extension points are marked
`TODO(candidate)` where present — e.g. resolving the *specific* governing rule
from `underwriting_decision.policy_doc_id -> policy_document -> policy_rule`
instead of the region-based approximation used by the ETL.
