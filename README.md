# AutoLoan-DocIntel — Senior AI/ML Engineer Take-Home

> An enterprise-grade, end-to-end take-home themed on **auto-loan / banking document intelligence**.
> Built and maintained by **Aelum Consulting**. Local/OSS-first (Ollama, Postgres+pgvector, Redis) — no cloud account required.

This repository has two halves:

| Path | Audience | Contents |
|------|----------|----------|
| [`challenge-pack/`](challenge-pack/) | **Candidate** (public) | The brief, the OSS docker stack, the labeled corpus, the ~100-table schema, the ontology, the FastAPI/HTMX app shell, the LangGraph skeleton, the API contract, and runnable eval CLIs. |
| [`grading-kit/`](grading-kit/) | **Evaluators** (private) | Hidden test set + answer keys, the automated grading harness (`scorecard.json`), the reference solution, the rubric, and the grader runbook. |
| [`challenge-wbs.xlsx`](challenge-wbs.xlsx) | Both | The work-breakdown structure: candidate 2h/day × 14-day plan, author prep plan, rubric, rollup. |

## What the candidate builds

A multi-agent assistant over scanned auto-loan documents and a relational loan warehouse:

1. **OCR extraction** — tables spanning multiple pages, skewed columns, cursive handwritten reviewer notes.
2. **NL-to-SQL** over a ~100-table / ~200-column auto-loan schema (schema-linking via OpenMetadata + pgvector).
3. **SPARQL / ontology** reasoning over auto-loan policy rules (Oxigraph).
4. **RAG + re-ranker** with grounded citations.
5. **ML underwriting** — a LightGBM probability-of-default model with SHAP adverse-action reasons.
6. **A 14-node LangGraph** orchestration with a human-in-the-loop gate.
7. **HTMX + FastAPI** app — argon2 auth, **Postgres-backed sessions**, structured JSON logging.
8. **A Redis-backed, horizontally-scalable API** — semantic cache, rate limiting, idempotency, Redis-Streams ingestion, SSE streaming.

See [`challenge-pack/README.md`](challenge-pack/README.md) for the full brief and [`challenge-pack/SUBMISSION.md`](challenge-pack/SUBMISSION.md) for deliverables.

## Quick start (candidate)

```bash
cd challenge-pack
cp .env.example .env
docker compose up -d          # postgres+pgvector, redis, ollama (+model pull), app, openmetadata
# open http://localhost:8000  (login page) and http://localhost:8585 (OpenMetadata)
```

## Effort & scope

Designed for **~2 focused hours/day over 14 days (≈28h)**. Heavy data/infra is provided so you implement the *intelligence + integration*, scored on a **MUST / Target / Stretch** ladder (pass = 70/100). See the WBS workbook and [`grading-kit/rubric.md`](grading-kit/rubric.md).

---
© Aelum Consulting. Internal hiring assessment — confidential. See [LICENSE](LICENSE).
