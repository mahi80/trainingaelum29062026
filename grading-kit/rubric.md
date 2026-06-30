# AutoLoan-DocIntel — Grading Rubric (100 points, pass = 70)

> Private evaluator document. Each line lists its **weight**, **scoring mode**
> (`auto` = computed by `harness/scorer.py`, `manual` = human review, `mixed` =
> auto signal a reviewer may adjust within the cap), **what earns full marks**,
> and **where it is measured**. The auto-grader writes one `scorecard.json`
> entry per line (`key` below) with `raw`, `points_awarded`, `max_points`.

| # | Line (`key`) | Pts | Mode |
|---|--------------|----:|------|
| 1 | NL-to-SQL (`nl2sql`) | 16 | mixed |
| 2 | OCR extraction (`ocr`) | 14 | mixed |
| 3 | LangGraph orchestration (`langgraph`) | 14 | mixed |
| 4 | RAG + reranker (`rag`) | 10 | mixed |
| 5 | App: HTMX + auth + sessions + logging (`app`) | 10 | mixed |
| 6 | Redis API (`redis_api`) | 10 | mixed |
| 7 | ML underwriting (`ml_underwriting`) | 8 | mixed |
| 8 | Engineering quality (`eng_quality`) | 8 | manual |
| 9 | SPARQL / ontology (`sparql`) | 4 | mixed |
| 10 | Security & red-team (`security`) | 4 | mixed |
| 11 | Docs / ADRs (`docs`) | 2 | manual |
| | **Total** | **100** | pass ≥ 70 |

---

## 1. NL-to-SQL — 16 pts (mixed)
**Full marks:** ≥ 0.85 execution accuracy on the hidden NL→SQL test set; schema-linking
uses OpenMetadata + pgvector (`app.schema_embedding`) rather than dumping the whole DDL;
generated SQL is read-only, parameterised, and passes the validator/repair loop.
**Scoring:** auto = up to 12 pts from `challenge-pack/eval/sql_eval.py` execution
accuracy (threshold ladder 0.30/0.50/0.70/0.85 → 25/50/75/100% of 12) + up to 4 pts
from schema conformance (required tables/columns/FKs present, from `test_conformance.py`).
**Measured:** `eval/sql_eval.py --split test`; `harness/test_conformance.py` schema tests.

## 2. OCR extraction — 14 pts (mixed)
**Full marks:** ≥ 0.85 TEDS-Struct on hidden test tables; correct grid-matrix cells
(row/col span, header flags); handwriting recovered (CER/WER reported for print vs
hand); multi-page tables stitched per `stitch.json`.
**Scoring:** auto via `eval/ocr_eval.py` primary metric (TEDS-Struct → cell-F1
fallback), ladder 0.30/0.50/0.70/0.85, **scaled by the data-integrity gate** (manifest
+ SHA256SUMS + no-leakage must pass, else the score is suspect and capped).
**Measured:** `eval/ocr_eval.py --split test --gt grading-kit/hidden/gt_test`.

## 3. LangGraph orchestration — 14 pts (mixed)
**Full marks:** all 14 nodes implemented (no `NotImplementedError`); router picks
`{sql,sparql,vector,underwrite,hybrid}` correctly; Redis checkpointer + resume works;
`interrupt_before=["hitl_gate"]` honoured (run pauses, resumes on approval);
aggregator/critic retry loop bounded; degraded-mode flag set on failures.
**Scoring:** auto presence check (import `src.graph.build_graph`, assert compiled graph
+ 14 nodes callable, no NotImplemented in `src/nodes`) → up to 7; manual demo of HITL
pause/resume + correct routing on the 10 demo questions → up to 7.
**Measured:** import probe in scorer diagnostics; manual demo (RUNBOOK step 6).

## 4. RAG + reranker — 10 pts (mixed)
**Full marks:** hybrid retrieval (BM25/trgm + 768-d pgvector) with a cross-encoder or
LLM reranker; answers cite real `doc.document_chunk` rows; nDCG@10 ≥ 0.70 and
groundedness/faithfulness high; no hallucinated citations.
**Scoring:** auto via `eval/rag_eval.py` (nDCG@10 / groundedness), ladder
0.30/0.50/0.70/0.85; manual spot-check that citations resolve to real chunks.
**Measured:** `eval/rag_eval.py --split test`; `GET /v1/search?rerank=true`.

## 5. App: HTMX + auth + Postgres sessions + logging — 10 pts (mixed)
**Full marks:** HTMX UI works; argon2 password hashing; OAuth2 password flow issues a
usable bearer; sessions stored in **`app.session` (Postgres)** with HttpOnly cookie,
exactly one row per login, deleted on logout, `expires_at` honoured; structlog JSON logs
carry `request_id` + `correlation_id`.
**Scoring:** auto = live pass rate of `harness/test_auth_session.py` × 10 (needs
`APP_BASE_URL` + `DATABASE_URL`); if app not running, line is manual.
**Measured:** `test_auth_session.py`; inspect a log line for the two ids.

## 6. Redis API — 10 pts (mixed)
**Full marks:** semantic/idempotency cache in Redis; `Idempotency-Key` dedupes POSTs;
token-bucket rate limit → 429 + RFC7807; `/v1/ingest` enqueues to Redis Streams and
returns 202 `{job_id}`; `GET /v1/ingest/{job_id}` reports progress; `/v1/chat?stream=true`
emits SSE; horizontally scalable (no in-process state).
**Scoring:** auto contract probes when app live (idempotency replay returns identical
body; 2nd rapid burst → 429; ingest → 202 with job_id; SSE content-type); else manual.
**Measured:** live probes (RUNBOOK step 5); `openapi/contract.yaml` conformance.

## 7. ML underwriting — 8 pts (mixed)
**Full marks:** LightGBM PD model trained on the warehouse; ROC-AUC ≥ 0.85 on holdout;
SHAP adverse-action reasons mapped to `ref.decision_reason.adverse_action_code`;
decision ∈ {approve, refer, deny} written to `loan.underwriting_decision`.
**Scoring:** auto via `eval/underwrite_eval.py` AUC, ladder 0.60/0.70/0.78/0.85;
manual check that SHAP reasons are sane and adverse-action-coded.
**Measured:** `eval/underwrite_eval.py --split test`; `underwriting_scorer` node output.

## 8. Engineering quality — 8 pts (manual)
**Full marks:** meaningful tests (unit + integration) that pass; clean module layout;
type hints + `py_compile`/mypy clean; docker compose comes up healthy first try; no
secrets committed; sensible commits.
**Scoring:** manual. Reviewer runs the candidate's own test suite and reads the diff.
**Measured:** `pytest` in the submission; `docker compose up`; code read.

## 9. SPARQL / ontology — 4 pts (mixed)
**Full marks:** loads `ontology/auto_loan.ttl` into Oxigraph; the `sparql_ontology_agent`
node answers a policy-rule question correctly, respecting `supersedes_rule_id` /
region precedence.
**Scoring:** auto presence probe (TTL parses, node not NotImplemented) → up to 2;
manual correctness on a policy question → up to 2.
**Measured:** scorer ontology probe; manual demo (RUNBOOK step 6).

## 10. Security & red-team — 4 pts (mixed)
**Full marks:** SQL-injection blocked (NL→SQL is read-only + validated); prompt-injection
in document text doesn't exfiltrate or escalate; SSN/PII stays tokenised (`ssn_tokenized`);
authz enforced on every protected route; secrets in env not code.
**Scoring:** auto = anon-route rejection from `test_auth_session.py` + a canned
injection probe; manual review of the candidate's `SECURITY.md` / red-team notes.
**Measured:** `test_auth_session.py::test_protected_route_requires_auth`; manual.

## 11. Docs / ADRs — 2 pts (manual)
**Full marks:** README runs as written; ≥ 2 ADRs explaining real trade-offs (e.g. why
pgvector, why Postgres sessions, reranker choice); `SUBMISSION.md` checklist complete.
**Scoring:** manual.
**Measured:** repo docs + ADR folder.

---

### Auto vs manual summary
- **Fully/partly auto (scorer.py):** `nl2sql`, `ocr`, `rag`, `ml_underwriting`,
  `app`, `redis_api` (when app live), plus the schema/data-integrity/leakage gates.
- **Manual (or auto-zero until reviewed):** `langgraph`, `sparql`, `security`
  (manual halves), `eng_quality`, `docs`.
- Lines left `null` in `scorecard.json` are the reviewer's TODO list
  (`totals.manual_lines_pending`).
