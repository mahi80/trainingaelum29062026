# Evaluation CLIs (`challenge-pack/eval`)

Candidate-runnable scorers for every track of AutoLoan-DocIntel. These are the
**same metrics the private grading kit uses**, so you can self-score before you
submit. All metric math is pure-python; heavy libraries (`lxml`, `sqlglot`, a
Postgres driver, `numpy`) are imported lazily and only by the CLI that needs them.

```
eval/
├── __init__.py
├── levenshtein.py        # edit distance + CER/WER + LCS helpers (no deps)
├── ocr_eval.py           # TEDS, TEDS-Struct, GriTS-Top/Con, cell-F1, CER/WER
├── sql_eval.py           # Execution Accuracy, Exact-Set-Match, valid-SQL rate
├── rag_eval.py           # Recall@40, MRR, nDCG@10 + pre/post-rerank lift
├── underwrite_eval.py    # AUC-ROC, PR-AUC, Brier, decision-agreement
├── sql_samples.json      # 8 PUBLIC multi-join NL->SQL gold pairs
├── qrels_sample.json     # tiny relevance-judgment sample for rag_eval
├── loadtest/
│   ├── chat_loadtest.js  # k6 script: 50 RPS against /v1/chat
│   └── README.md
├── requirements.txt
└── README.md             # this file
```

Run every CLI as a module from the **challenge-pack root** so the `eval` package
imports cleanly:

```bash
cd challenge-pack
python -m eval.ocr_eval --help
```

## Install

```bash
pip install -r eval/requirements.txt
# For sql_eval Execution Accuracy, also install a Postgres driver:
pip install "psycopg[binary]"   # or: pip install psycopg2-binary
```

---

## `ocr_eval` — document AI / table extraction

Both `--pred` and `--gt` are **directories** of per-page files named
`page_XXXX.<ext>`. Write your model output in the same formats as the ground
truth (`example/gt`):

| File                   | Drives                       |
|------------------------|------------------------------|
| `page_XXXX.tables.html`| TEDS, TEDS-Struct            |
| `page_XXXX.cells.json` | GriTS-Top/Con, cell-F1       |
| `page_XXXX.hocr`       | CER / WER (print vs handwritten) |

```bash
python -m eval.ocr_eval --pred runs/ocr_out --gt example/gt
python -m eval.ocr_eval --pred runs/ocr_out                 # gt defaults to example/gt
python -m eval.ocr_eval --pred runs/ocr_out --pages page_0001 page_0002
```

- **TEDS / TEDS-Struct** — tree-edit-distance similarity over the HTML table
  tree; `Struct` nulls cell text so only grid topology is scored.
- **GriTS-Top / GriTS-Con** — grid-cell matching from `cells.json`: `Top` uses
  bbox IoU between matched cells, `Con` uses a char-LCS over cell text.
- **cell-F1** — precision/recall/F1 over `(row, col, text)` cell tuples.
- **CER / WER** — reported **separately** for printed vs handwritten tokens,
  using the hOCR `class="... handwritten"` annotation.

> Ground truth lives only for the TRAIN split in `example/gt`. The TEST answers
> are withheld in `grading-kit/hidden/gt_test/`.

---

## `sql_eval` — NL → SQL

```bash
# structure-only metrics (no DB required)
python -m eval.sql_eval --pred preds.json

# full metrics incl. Execution Accuracy (headline)
export DATABASE_URL=postgresql://app:app@localhost:5432/autoloan
python -m eval.sql_eval --gold grading-kit/hidden/sql_gold.json --pred preds.json
```

- `--gold` defaults to `eval/sql_samples.json` (8 **public** multi-join pairs) so
  the CLI runs out of the box. The grader uses the 20 hidden pairs in
  `grading-kit/hidden/sql_gold.json`.
- `--pred` is a JSON mapping `{question: predicted_sql}`.

Metrics: **Execution Accuracy** (run gold vs pred, compare result rows as an
order-insensitive multiset), **Exact-Set-Match** (order-insensitive comparison of
parsed SELECT/FROM/WHERE/… clauses via `sqlglot`), **valid-SQL rate** (fraction
that parse under the Postgres dialect).

---

## `rag_eval` — retrieval

```bash
python -m eval.rag_eval --qrels eval/qrels_sample.json --run run.json
```

- `--qrels`: `{qid: [docids]}` (binary) or `{qid: {docid: gain}}` (graded).
- `--run`: `{qid: [ranked docids]}`, or `{qid: {pre_rerank: [...], post_rerank: [...]}}`
  to measure reranker **lift**.

Metrics: **Recall@40**, **MRR**, **nDCG@10**. When both pre- and post-rerank
lists are present, the per-metric lift (post − pre) is reported.

---

## `underwrite_eval` — risk model

```bash
python -m eval.underwrite_eval --pred preds.csv --labels labels.csv
```

- `preds.csv` columns: `pd_score` (float in [0,1]), `decision`
  (approve/decline/refer), optional `id`.
- `labels.csv` columns: `default` (1 = bad, 0 = good), optional `decision`,
  optional `id`. Rows align by `id` when both files have it, else by order.

Metrics: **AUC-ROC** (rank-based, ties handled), **PR-AUC** (average precision),
**Brier** score, **decision-agreement**. Pure python — `sklearn` is optional.

---

## `loadtest/` — k6 chat load test

See `loadtest/README.md`. Drives 50 RPS at `POST /v1/chat` with `stream=false`
(so the p95 latency budget excludes token generation):

```bash
k6 run eval/loadtest/chat_loadtest.js
```

Thresholds: `http_req_duration p(95) < 150 ms`, `http_req_failed rate < 0.01`.

---

## Output

Every Python CLI prints a JSON summary to stdout and exits non-zero on bad input
(missing files / dirs), so they compose cleanly in scripts and CI.
