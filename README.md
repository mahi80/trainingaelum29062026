# AutoLoan-DocIntel — Senior AI/ML Engineer Take-Home

> An enterprise-grade, end-to-end take-home themed on **auto-loan / banking document intelligence**.
> Built and maintained by **Aelum Consulting**. Local/OSS-first (Ollama, Postgres+pgvector, Redis) — no cloud account required.

This repository has two halves:

| Path | Audience | Contents |
|------|----------|----------|
| [`challenge-pack/`](challenge-pack/) | **Candidate** (public) | The brief, the OSS docker stack, the labeled corpus, the ~100-table schema, the ontology, the FastAPI/HTMX app shell, the LangGraph skeleton, the API contract, and runnable eval CLIs. |
| [`grading-kit/`](grading-kit/) | **Evaluators** (private) | Hidden test set + answer keys, the automated grading harness (`scorecard.json`), the reference solution, the rubric, and the grader runbook. |
| [`challenge-wbs.xlsx`](challenge-wbs.xlsx) | Both | The work-breakdown structure: candidate 2h/day × 14-day plan, author prep plan, rubric, rollup. |
| [`consultant-weekly-tracker.xlsx`](consultant-weekly-tracker.xlsx) | Evaluators | Per-consultant weekly status (Week-1/Week-2 RAG, tier, score) + cohort dashboard. Auto-fill with [`grading-kit/harness/consultant_status.py`](grading-kit/harness/consultant_status.py). |

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

## Getting started (candidates)

**Never commit to `main`.** Create your own solution branch and open a Pull Request when done.

```bash
# 1. Clone (or fork — see note below if you only have read access)
git clone https://github.com/mahi80/trainingaelum29062026.git
cd trainingaelum29062026

# 2. Create your solution branch (use your name / handle)
git checkout -b solution/<your-name>

# 3. Bring up the OSS stack and start building
cd challenge-pack
cp .env.example .env
docker compose up -d                            # postgres+pgvector, redis, ollama, app
docker compose --profile seed run --rm seed     # load demo data (run once)
# open http://localhost:8000   (login: admin / ChangeMe123!)

# 4. Commit early and often — we review commit history (see SUBMISSION.md)
git add -A && git commit -m "feat: implement SQL vertical slice"

# 5. Push your branch and open a PR into main
git push -u origin solution/<your-name>
gh pr create --base main --head solution/<your-name> --title "Solution: <your-name>" --fill
```

> **No write access to this repo?** Fork it on GitHub, push your
> `solution/<your-name>` branch to your fork, then open the PR from your fork
> into this repo's `main`.

> Work only inside [`challenge-pack/`](challenge-pack/). Do **not** modify
> [`grading-kit/`](grading-kit/) — it's for evaluators. First boot pulls the
> Ollama models (`llama3.1:8b`, `qwen2.5-coder:7b`, `nomic-embed-text`), so the
> first LLM call may block while they download.

## Effort & scope

Designed for **~2 focused hours/day over 14 days (≈28h)**. Heavy data/infra is provided so you implement the *intelligence + integration*, scored on a **MUST / Target / Stretch** ladder (pass = 70/100). See the WBS workbook and [`grading-kit/rubric.md`](grading-kit/rubric.md).

---
© Aelum Consulting. Internal hiring assessment — confidential. See [LICENSE](LICENSE).
