# `example/` — the labeled document corpus (provided)

100 "scanned" auto-loan / banking pages with aligned ground truth. **You do not
create data** — you build the OCR/extraction pipeline that reproduces this GT,
then everything downstream (load → NL-to-SQL → RAG → underwriting → API).

## Layout
```
images/page_XXXX.jpg     # the scans you process (all 100 are public)
gt/page_XXXX.cells.json  # TRAIN pages only: grid-matrix table GT
gt/page_XXXX.tables.html # TRAIN pages only: PubTabNet-style HTML (TEDS)
gt/page_XXXX.hocr        # TRAIN pages only: hOCR words (CER/WER)
gt/page_XXXX.alto.xml    # TRAIN pages only: ALTO v4
gt/page_XXXX.meta.json   # ALL pages: provenance (seed, homography, distortions)
manifest.jsonl           # one row per page incl. "split": train|test
stitch.json              # multi-page table continuation links
SHA256SUMS               # integrity of public files
```

## Document classes
| Class | ~Pages | Hard features |
|-------|--------|---------------|
| `application` | 40 (single-page) | handwritten fields + signature, vehicle table |
| `verification` | 30 (multi-page) | bank-statement table spanning 2–5 pages, dense cells |
| `policy` | 30 (multi-page) | wide FICO×LTV rate matrix, skewed columns, margin notes |

## Splits & the hidden test set
`manifest.jsonl` marks each page `train` or `test` (80/20). **Test-page answer
GT is withheld** (held by evaluators) — the test split over-samples the hard
cases (multi-page continuation, perspective, handwriting). Train GT is provided
so you can develop and self-check. The grader scores your pipeline on the hidden
test pages. Do not attempt to reconstruct withheld GT.

## Ground-truth formats (quick reference)
- **cells.json**: `{table_uid, cells:[{row_start,row_end,col_start,col_end,bbox,text,is_header,is_handwritten}]}`. `bbox = [x0,y0,x1,y1]` in image pixels.
- **tables.html**: a single `<table>` with `rowspan`/`colspan` — feed to TEDS.
- **hocr / alto.xml**: word-level boxes; handwriting carries `class="handwritten"` (hOCR) / `STYLE="handwritten"` (ALTO).
- **meta.json**: includes the 3×3 `homography` applied to the clean page — useful for debugging alignment, not needed for extraction.

## Metrics you'll be scored on
TEDS-Struct, GriTS-Top/Con, cell-F1 (structure); CER/WER reported separately for
print vs handwriting; multi-page stitch-reconstruction F1 (see `stitch.json`).
Run `eval/ocr_eval.py` against the train split to self-check.

Regenerate with `tools/docgen/generate.py` then `tools/docgen/split.py`.
