# AutoLoan-DocIntel — Candidate Brief

> **Role:** Senior AI/ML Engineer take-home.
> **Theme:** document intelligence + agentic RAG over **auto-loan / banking** data.
> **Stack:** local/OSS-first — Python 3.11, FastAPI + HTMX, PostgreSQL 16 + pgvector,
> Redis 7, Ollama (local LLM), LangGraph. **No cloud account required.**

---

## 1. The scenario — "RegLoan"

You are joining **RegLoan**, a regional auto-lender. Loan officers drown in scanned
paperwork: credit applications, income/bank verification statements, and
underwriting policy & rate sheets. The documents are *hard* — tables that span
several pages, skewed/rotated columns from bad scans, dense multi-line cells, and
**handwritten reviewer notes**. Alongside the paper sits a ~100-table relational
**loan warehouse** (borrowers, vehicles, applications, decisions, payments,
policy rules).

Your job: build the **AutoLoan-DocIntel assistant** — a multi-agent system that
ingests the scans, answers natural-language questions over *both* the documents
and the warehouse, reasons over policy rules, and produces **cited, auditable**
underwriting answers.

You answer questions like:

- *"What's the DTI on application AP-100204 and did it pass policy?"* → SQL + policy
- *"Show the income figures from the borrower's bank statement, page 3."* → OCR + RAG
- *"Which approved loans in the West region violate the superseded rate rule?"* → SPARQL + SQL
- *"Underwrite AP-100311 and explain the decision."* → underwrite lane + citations

---

## 2. What to build — the 8 capabilities

| # | Capability | Where it lives |
|---|------------|----------------|
| 1 | **OCR extraction** of multi-page, skewed tables + handwriting | your code + `example/` GT |
| 2 | **NL→SQL** over the ~100-table schema (schema-linking) | `src/nodes` SQL lane |
| 3 | **SPARQL / ontology** reasoning over policy rules | `src/nodes` graph lane (`ontology/`) |
| 4 | **Hybrid retrieval** (pgvector + keyword) with **reranking** | `src/nodes` RAG lane |
| 5 | **Underwriting scorer** (DTI / LTV / PD → approve·refer·deny) | `src/nodes` ML lane |
| 6 | **Policy/compliance check** + **HITL gate** for flagged cases | `src/nodes` governance lane |
| 7 | **Agent orchestration** — route, repair-loop, critic, citations | `src/graph` (provided wiring) |
| 8 | **Serving** — implement `openapi/contract.yaml` (FastAPI + HTMX) | `app/` |

Cross-cutting (already scaffolded for you, keep them working): **auth** (argon2 +
OAuth2 password flow), **sessions in Postgres** (`app.session`), **structlog JSON
logging** with `request_id` + `correlation_id`, Redis-backed cache / rate-limit /
idempotency / **Redis-Streams ingestion**.

---

## Example datasets provided

You don't create data — every modality ships with concrete examples plus a runnable
evaluator. Public samples are for building & self-checking; matching **hidden** gold is
held by evaluators (same pattern everywhere).

| Modality | Example files | Contents | Evaluator |
|----------|---------------|----------|-----------|
| **Scanned documents (OCR)** | `example/images/*.jpg` (100) + `example/gt/` | 40 applications · 30 verification · 30 policy; multi-page tables, skew/perspective, cursive handwriting (70 pages have handwriting). GT per page: `cells.json` (grid), `tables.html` (PubTabNet), `hocr`, `alto.xml`. **80 train / 20 test** (test GT withheld). `manifest.jsonl`, `stitch.json`. | `eval/ocr_eval.py` (TEDS/GriTS/CER) |
| **Relational warehouse (NL→SQL grounding)** | `db/ddl/` + `db/seed/seed.py` | **108-table / 589-col** auto-loan schema + ~5k deterministic Faker rows (borrowers, applications, vehicles, credit, decisions, loans, payments, policy rules), pgvector columns. | `grading-kit/harness/test_conformance.py` |
| **NL-to-SQL** | `eval/sql_samples.json` (**8 public**) | Natural-language ↔ gold Postgres SQL pairs requiring multi-table joins. Hidden: `sql_gold.json` (**20**). | `eval/sql_eval.py` (execution accuracy) |
| **SPARQL / ontology** | `ontology/sparql_samples.json` (**8 public**) + `ontology/auto_loan.ttl` | NL ↔ SPARQL over the policy ontology (transitive `supersedes`, region reasoning). Hidden: `sparql_gold.json` (**15**). | `ontology/load_oxigraph.py` |
| **RAG retrieval labels** | `eval/qrels_sample.json` | Query → relevant-chunk relevance labels. | `eval/rag_eval.py` (Recall@k/MRR/nDCG) |
| **ML underwriting (PD)** | `example/underwriting/train.csv` (**1000**) · `test.csv` (**250**) | 12 features — `fico, dti, ltv, loan_amount, term_months, vehicle_age, annual_income, prior_delinquencies, inquiries_6mo, down_payment_pct, employment_years, region_risk_tier` — + binary `default` label. Learnable (reference **AUC ≈ 0.80**). Hidden: `underwriting_holdout.csv` (**300**). Regenerate: `python tools/mk_underwriting.py`. | `eval/underwrite_eval.py` (AUC/PR-AUC/Brier) |

Quick self-checks:
```bash
python eval/ocr_eval.py --pred example/gt --gt example/gt            # sanity (perfect on GT)
python eval/underwrite_eval.py --pred my_preds.csv --labels example/underwriting/test.csv
```

---

## 3. Tiers — MUST / Target / Stretch

Grade yourself against [`grading-kit/rubric.md`](../grading-kit/rubric.md).

**MUST (pass bar)**
- `docker compose up` brings up postgres + redis + ollama + app; `/healthz` & `/readyz` green.
- The **SQL vertical slice** works end-to-end: `router → schema_linker → nl2sql →
  sql_validator_repair → sql_executor → explainer_citation` returns a cited answer.
- OCR extracts at least the **grid-matrix cells** for clean (undistorted) tables.
- Hybrid vector search over ingested chunks returns relevant pages.
- Auth + sessions + structured logging intact; secrets only via env.

**Target (solid senior)**
- Reranking measurably improves retrieval; multi-page table **stitching** works.
- Underwriting lane computes DTI/LTV/PD and returns `approve|refer|deny` with reasons.
- `/v1/chat` **SSE streaming**, idempotency, and rate-limiting honored.
- Handwriting recognized on a meaningful fraction of pages.
- ≥3 ADRs; clean commit history; tests for the SQL + RAG lanes.

**Stretch (standout)**
- SPARQL/ontology lane answers policy questions incl. **superseded-rule** logic.
- HITL gate (durable interrupt) with resume; aggregator/critic retry loop tuned.
- Distorted/perspective tables handled; TEDS score competitive on the test split.
- OpenMetadata catalog ingestion driving schema-linking (see below).

---

## 4. How to run

**First, create your own solution branch — never commit to `main`:**

```bash
git clone https://github.com/mahi80/trainingaelum29062026.git
cd trainingaelum29062026
git checkout -b solution/<your-name>     # your working branch
cd challenge-pack
```

> No write access? Fork on GitHub, push your branch to the fork, and open the PR
> from there. Work only inside `challenge-pack/`; leave `grading-kit/` alone.

**Then bring up the stack:**

```bash
cp .env.example .env                  # safe local defaults; edit if you like
docker compose up -d                  # postgres + redis + ollama + ollama-pull + app
docker compose --profile seed run --rm seed   # load demo data (run once; users: admin / ChangeMe123!)
docker compose logs -f app            # watch it come up
curl localhost:8000/healthz           # liveness
curl localhost:8000/readyz            # readiness (PG + Redis + Ollama)
```

**Commit often and submit a PR when done** (we review commit history):

```bash
git add -A && git commit -m "feat: <what you did>"
git push -u origin solution/<your-name>
gh pr create --base main --head solution/<your-name> --title "Solution: <your-name>" --fill
```

First boot pulls three Ollama models (`llama3.1:8b`, `qwen2.5-coder:7b`,
`nomic-embed-text`) — this can take a while; the API is up immediately and the
first LLM call blocks on the pull. The DDL in `db/ddl/` auto-loads into a fresh
Postgres volume.

**Seed the warehouse** (once, after a fresh `up` — DDL creates the empty schema;
this loads ~5k referentially-consistent rows and maps the 100 scans to
`doc.document` / `doc.document_page`):

```bash
docker compose --profile seed run --rm seed    # one-shot; seed users: admin / ChangeMe123!
```

**Generate the corpus locally** (the train split + GT ship in `example/`):

```bash
pip install -r tools/requirements.txt
python tools/docgen/generate.py --out example --seed 1337           # full 100 pages
python tools/docgen/generate.py --out example --seed 1337 --limit 6 # quick smoke
```

**Optional / heavy** (off by default — only if your solution uses them):

```bash
docker compose --profile storage up -d minio                        # object store
docker compose -f docker-compose.yml -f openmetadata.compose.yml \
  --profile catalog up -d                                           # data catalog
```

---

## 5. Repo map

| Path | What it is |
|------|------------|
| [`README.md`](README.md) | This brief. |
| [`SUBMISSION.md`](SUBMISSION.md) | **Required deliverables**, demo checklist, ADR + anti-cheat + security checklist. |
| [`docker-compose.yml`](docker-compose.yml) | The OSS dev stack (postgres/redis/ollama/app, + optional minio). |
| [`openmetadata.compose.yml`](openmetadata.compose.yml) | Optional heavy data-catalog override + ingestion notes. |
| [`.env.example`](.env.example) | All env vars with safe local defaults. |
| [`db/ddl/`](db/ddl/) | The ~100-table loan warehouse schema (ref/loan/doc/app + pgvector). |
| [`example/`](example/) | 100 scanned pages + GT (train split) + `manifest.jsonl` + `stitch.json`; `underwriting/` PD dataset (train/test CSVs). |
| [`openapi/contract.yaml`](openapi/contract.yaml) | The **fixed serving contract** you implement. |
| [`ontology/`](ontology/) | Auto-loan policy ontology + SPARQL assets. |
| [`src/`](src/) | LangGraph skeleton (`graph/`), LLM interface (`llm/`), 14 nodes (`nodes/` — you build the bodies). |
| [`app/`](app/) | FastAPI + HTMX shell (auth, sessions, middleware, templates). |
| [`tools/`](tools/) | `docgen/` (scan generator), `schemagen/` (schema expander), `mk_underwriting.py` (PD dataset). |
| [`eval/`](eval/) | Runnable evaluation CLIs (OCR/TEDS, retrieval, SQL, underwriting). |
| [`.github/workflows/ci.yml`](.github/workflows/ci.yml) | Offline CI: ruff, schema-size, docgen smoke, import test, contract validate. |

Start with the **SQL vertical slice**, then add the RAG, underwriting, and
graph lanes. The graph wiring in `src/graph/build.py` is provided; you implement
the node bodies in `src/nodes/` (the candidate ones currently raise
`NotImplementedError`).

---

## 6. Effort & expectations

Designed for **~2 hours/day × 14 days ≈ 28 hours**. We are not expecting all
stretch goals. We *are* looking for: a working MUST tier, clear engineering
judgment (the **ADRs** matter — see `SUBMISSION.md`), production hygiene
(secrets, logging, tests, idempotency), and an honest demo.

When you're done, follow **[`SUBMISSION.md`](SUBMISSION.md)** and grade yourself
against **[`grading-kit/rubric.md`](../grading-kit/rubric.md)**.

Good luck — build the slice that proves it works, then deepen it.
