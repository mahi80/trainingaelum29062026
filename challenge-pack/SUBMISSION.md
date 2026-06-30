# Submission Guide

Read the [brief](README.md) first. This file tells you **exactly what to hand in**,
how to lay it out, what to demo, and the bars we check for ADRs, anti-cheat, and
security. Grade yourself against [`grading-kit/rubric.md`](../grading-kit/rubric.md).

---

## 1. Required deliverables

1. **Working code** in a git repo (history intact — see anti-cheat).
2. **`docker compose up` runs** with no manual surgery beyond `cp .env.example .env`.
   `/healthz` and `/readyz` must go green.
3. **The contract implemented** — the endpoints in `openapi/contract.yaml`, unchanged.
   At minimum the SQL vertical slice through `/v1/chat`.
4. **Tests** — at least the SQL lane and the RAG lane, runnable with `pytest`.
5. **≥ 3 ADRs** under `docs/adr/` (see §4).
6. **`RESULTS.md`** — your self-scored eval numbers (OCR/TEDS, retrieval, SQL exec
   accuracy, underwriting) produced by the `eval/` CLIs, with the command lines used.
7. **A demo recording** (≤ 10 min, §3).
8. **`SECURITY.md`** — your filled-in red-team checklist (§5).

---

## 2. Repo layout to submit

Keep the provided structure; add your implementation and docs:

```
your-submission/
├── README.md                # how to run YOUR solution (1-screen quickstart)
├── RESULTS.md               # self-scored eval numbers + commands
├── SECURITY.md              # red-team checklist, filled in
├── docker-compose.yml       # yours (may extend the provided one)
├── .env.example             # no real secrets
├── app/                     # FastAPI + HTMX implementation (app.main:app)
├── src/                     # node bodies you implemented
├── tests/                   # pytest: SQL lane, RAG lane, (more = better)
├── docs/
│   ├── adr/                 # >= 3 ADRs (0001 provided; add yours)
│   └── demo.mp4 | demo-link.txt
└── (db/, ontology/, openapi/, tools/, example/ as provided — note any changes)
```

If you change anything we provided (contract, schema, graph wiring), **say so and
say why** in an ADR — don't silently fork the contract; grading assumes it.

---

## 3. What to record in the demo

A screen recording (≤ 10 min, talk over it). Hit these beats:

1. `docker compose up` → `/healthz` + `/readyz` green (10-20s of it coming up is fine).
2. **SQL slice**: ask a warehouse question via `/v1/chat`; show the generated SQL,
   the rows, and the **citations**.
3. **OCR + RAG**: ingest a page from `example/`, then ask a question whose answer
   lives in that scan (bonus: a handwritten field or a multi-page table).
4. **Underwriting**: underwrite one application; show DTI/LTV/PD and the
   approve/refer/deny decision **with reasons**.
5. Any **Target/Stretch** you did (SSE streaming, reranking lift, SPARQL/superseded
   rule, HITL resume).
6. 30-second tour of your **ADRs** — the one decision you're most proud of and the
   one tradeoff you'd revisit.

Show real terminal/UI output, not slides. Honesty about what *doesn't* work scores
better than a polished fake.

---

## 4. ADRs — the "why" bar (≥ 3 required)

Use the template in [`docs/adr/0001-record-architecture-decisions.md`](docs/adr/0001-record-architecture-decisions.md).
We seeded two example titles you may use or replace:

- [`0002-sql-vs-sparql-routing.md`](docs/adr/0002-sql-vs-sparql-routing.md)
- [`0003-redis-streams-vs-queue-for-ingestion.md`](docs/adr/0003-redis-streams-vs-queue-for-ingestion.md)

A good ADR states the **context**, the **options you weighed**, the **decision**,
and the **consequences/tradeoffs** — including what you gave up. Generic ADRs that
restate the obvious score low. We read these closely; they're how we tell senior
judgment from a working demo.

---

## 5. Security / red-team checklist

Put your answers in `SECURITY.md`. At minimum address:

- **Prompt injection** — a malicious instruction inside an ingested document (or a
  user turn) must not exfiltrate data, escape the SQL allow-list, or override the
  system prompt. Describe your guardrails and show one blocked attempt.
- **SQL safety** — NL→SQL is read-only / parameterized / schema-scoped; no DDL/DML,
  no cross-schema escapes. How does `sql_validator_repair` enforce it?
- **Secrets** — none committed; all via env; `.env` git-ignored; no secrets in logs.
- **PII** — SSNs are tokenized in the schema (`party.ssn_tokenized`); don't
  de-tokenize into responses or logs. Note any PII redaction in `structlog`.
- **AuthN/Z** — argon2 hashing, OAuth2 password flow, session expiry honored;
  protected endpoints reject anonymous/expired sessions.
- **SCA / supply chain** — pin deps; run a dependency audit (e.g. `pip-audit`) and
  report findings. Don't introduce unpinned or abandoned packages.
- **Rate limiting & idempotency** — abuse of `/v1/chat` and `/v1/ingest` is bounded;
  `Idempotency-Key` dedupes retried POSTs.

---

## 6. Anti-cheat

We want *your* engineering. The following are checked:

- **Commit history** — we expect incremental commits over the work window, not a
  single "initial commit" dump. Squashed-to-one or back-dated history is a flag.
- **ADR "why"** — ADRs must reflect decisions visible in your code and commits. ADRs
  that don't match the implementation are a flag.
- **Similarity check** — submissions are compared against each other, against the
  provided scaffold, and against public solutions. Copy-paste without understanding
  shows up fast in follow-up questions.
- **Demo correspondence** — the demo must run against the submitted code.

Using LLM assistants is fine (we do too) — but you own every line, you can explain
it, and you can extend it live if asked. Attribute any substantial external code.

---

Questions about scope? Default to the **MUST tier done well** over many half-built
stretch goals. Ship the slice that proves it works, then deepen it.
