# Grader RUNBOOK — scoring ONE AutoLoan-DocIntel submission

> Private. Follow top-to-bottom. Target ≈ 45–60 min/submission: ~15 min automated,
> the rest manual lines + demo Q&A. Pass mark = **70/100**.

## 0. Prerequisites (once per grader machine)
- Docker + docker compose, Python 3.11, `git`.
- Create the harness venv and install deps:
  ```bash
  cd <repo>/grading-kit/harness
  python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
  pip install -r requirements.txt
  ```
- Keep this repo's `grading-kit/hidden/gt_test/` (the withheld answer keys) — the
  scorer reads it for OCR scoring. Do NOT copy it into the candidate tree.

## 1. Get the submission
```bash
git clone <candidate-repo-url> submission && cd submission
git log --oneline -5          # sanity: real history, not one squashed dump
```
Note the commit SHA you are grading.

## 2. Bring the stack up
```bash
cd submission/challenge-pack
cp .env.example .env
docker compose up -d           # postgres+pgvector, redis, ollama (+pull), app, openmetadata
```
Wait until healthy (do not start grading early):
```bash
# poll readiness; app should answer 200 on /readyz when Redis+PG+Ollama are reachable
until curl -fsS http://localhost:8000/readyz >/dev/null 2>&1; do sleep 5; done
docker compose ps             # confirm all services Up/healthy
```
If the stack will not come up after a genuine attempt, record it: `app`, `redis_api`,
live `nl2sql/ocr/rag/ml` auto-lines fall back to **0/manual**; continue with the
static lines (schema, leakage, docs, code read).

## 3. Export grading env
```bash
export SUBMISSION_ROOT="$(pwd)/.."                       # repo root of the submission
export APP_BASE_URL="http://localhost:8000"
export DATABASE_URL="postgresql://app:app@localhost:5432/autoloan"  # match candidate .env
export REDIS_URL="redis://localhost:6379/0"
# optional: GRADER_USERNAME / GRADER_PASSWORD if the seed user differs
```

## 4. Run the auto-grader
```bash
cd "$SUBMISSION_ROOT/grading-kit/harness"
python scorer.py --submission "$SUBMISSION_ROOT" \
                 --out "$SUBMISSION_ROOT/grading-kit/scorecard.json"
```
The scorer:
- runs `test_conformance.py` (schema ≥100 tables/≥200 cols, required tables/cols/FKs,
  manifest, SHA256SUMS, **leakage check**);
- runs candidate eval CLIs under `challenge-pack/eval/` (`ocr_eval.py`, `sql_eval.py`,
  `rag_eval.py`, `underwrite_eval.py`) and converts metrics → points;
- runs `test_auth_session.py` live (since `APP_BASE_URL` is set);
- writes `scorecard.json`. It **never crashes** on a missing part — that part scores 0.

You can also run the suites directly for detail:
```bash
pytest test_conformance.py -v
pytest test_auth_session.py -v        # needs APP_BASE_URL (+ DATABASE_URL for DB checks)
```

## 5. Read the scorecard + live API spot-checks
```bash
cat "$SUBMISSION_ROOT/grading-kit/scorecard.json"
```
Check `totals.auto_points_awarded`, `totals.provisional_pass`, and
`totals.manual_lines_pending` (the lines you must score by hand).

Quick live contract probes (back the `redis_api` line):
```bash
# get a bearer
TOK=$(curl -fsS -X POST "$APP_BASE_URL/token" \
      -d 'grant_type=password&username=grader&password=grader-password' | jq -r .access_token)
# idempotency: same key twice → identical body
curl -fsS -X POST "$APP_BASE_URL/v1/chat" -H "Authorization: Bearer $TOK" \
     -H 'Idempotency-Key: grade-1' -d '{"message":"hi","stream":false}'
curl -fsS -X POST "$APP_BASE_URL/v1/chat" -H "Authorization: Bearer $TOK" \
     -H 'Idempotency-Key: grade-1' -d '{"message":"hi","stream":false}'
# rate limit: rapid burst should yield a 429 + application/problem+json
for i in $(seq 1 50); do curl -s -o /dev/null -w '%{http_code}\n' \
     -X POST "$APP_BASE_URL/v1/chat" -H "Authorization: Bearer $TOK" \
     -d '{"message":"x","stream":false}'; done | sort | uniq -c
# async ingest → 202 {job_id}; then poll status
curl -fsS -X POST "$APP_BASE_URL/v1/ingest" -H "Authorization: Bearer $TOK" \
     -d '{"paths":["example/images/page_0001.jpg"]}'
# SSE streaming content-type
curl -fsS -N -X POST "$APP_BASE_URL/v1/chat" -H "Authorization: Bearer $TOK" \
     -d '{"message":"stream please","stream":true}' -D - | head
```
Confirm a structlog JSON log line carries both `request_id` and `correlation_id`:
```bash
docker compose logs app | grep -m1 correlation_id
```

## 6. Manual lines + demo Q&A
Score the `manual_lines_pending` against `rubric.md`:
- **langgraph (14):** import probe in `scorecard.json.diagnostics`; then demo HITL —
  ask a question that routes to `underwrite`, confirm the run **pauses at `hitl_gate`**
  and resumes after approval. Check routing across the 5 routes.
- **sparql (4):** ask a policy question; verify the answer respects
  `supersedes_rule_id` / region precedence over `ontology/auto_loan.ttl`.
- **security (4):** try a SQL-injection NL prompt (must be blocked/read-only) and a
  document-embedded prompt-injection (must not leak/escalate); confirm `ssn_tokenized`
  never returns raw PII; read the candidate's `SECURITY.md`.
- **eng_quality (8):** run the candidate's own `pytest`; read the diff; check types,
  layout, compose health, no committed secrets.
- **docs (2):** README runs as written; ≥ 2 ADRs with real trade-offs;
  `SUBMISSION.md` complete.

Run the 10 demo questions (see `grading-kit/reference-solution/` for expected behaviour)
and note answer quality + citation correctness; this informs `rag`, `nl2sql`,
`langgraph` manual adjustments (within each cap).

## 7. Finalise
- Enter manual points into `scorecard.json` (replace each `null` `points_awarded`).
- Sum to a final score; `>= 70` = pass.
- Tear down: `docker compose down -v` in `challenge-pack/`.
- Record: commit SHA graded, final score, one-paragraph rationale, and any line where
  you overrode the auto value (with reason).

## 8. Weekly tracking (during the 14 days)
Track each consultant's progress at the two weekly check-ins, not just at the end.

1. **Auto-pull status** — from the repo root, after `git fetch --all --prune`:
   ```bash
   python grading-kit/harness/consultant_status.py --csv status.csv
   # or pin a common cohort start date:
   python grading-kit/harness/consultant_status.py --start 2026-07-01 --csv status.csv
   ```
   For every `solution/<name>` branch it reports **commits in Week 1 vs Week 2**, total
   commits ahead of `main`, last activity, open **PR + CI** status (if `gh` is installed),
   and **flags any branch that modified `grading-kit/`** (candidates must not — red flag).
   Output is a Markdown table + a CSV.
2. **Update the tracker** — open [`../consultant-weekly-tracker.xlsx`](../consultant-weekly-tracker.xlsx)
   (repo root; ships with 3 shaded EXAMPLE consultants — overwrite them). Two views:
   - **Weekly Status** tab: one row per consultant — paste commit columns from `status.csv`,
     set **Wk1 / Wk2 RAG** (Green/Amber/Red), `% complete`, blockers (`Due` and `Pass?` compute).
   - **Milestone Matrix** tab: the granular view — set each of the 11 capabilities to
     Done / WIP / Blocked / Not started per consultant; `% Done` computes automatically.
   The **Weekly Checklist** tab defines what "on track" means each week (Week 1 → MUST slice;
   Week 2 → Target+).
3. **Read the cohort Dashboard** tab (on-track / at-risk / behind, passing count, average
   score) to decide who needs a nudge or an early conversation.
4. At the end, enter **Tier reached** + **Score /100** (from §7) per consultant; `Pass?`
   computes automatically (>= 70).
