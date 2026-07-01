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

## Document classes (9 types; `doc_class` in the manifest is also a classification label)
| Class | Pages | Hard features |
|-------|------:|---------------|
| `application` | 16 | handwritten fields + signature, **checkboxes**, vehicle table, redacted SSN |
| `verification` | 21 (multi-page) | bank-statement table spanning 2–5 pages, **colspan group headers**, dense cells |
| `policy` | 13 (multi-page) | wide FICO×LTV rate matrix (colspan header), skewed columns, margin notes |
| `paystub` | 10 | **rowspan** category cells (Earnings/Deductions) + colspan (Current/YTD) |
| `w2` | 8 | boxed W-2 form, numbered rows, redacted SSN |
| `dealer_invoice` | 8 | line-item table with colspan charge group, handwritten total |
| `driver_license` | 8 | ID-card layout, photo box, key-value + signature (no table) |
| `vehicle_title` | 6 | title key-value fields + signature (no table) |
| `insurance_card` | 10 | small ID-card key-value fields (no table) |

**Visual variation** (recorded per page in `meta.json` / manifest): skew · rotation ·
perspective · a few pages rotated **90°/180°** (GT remapped) · semi-transparent
**stamps** (APPROVED/PAID/COPY…) and **watermarks** · **redaction bars** (redacted
values appear as a `[REDACTED]` token, `class="redacted"`) · scan **artifacts**
(hole punches, staples, creases, coffee stains, photocopy/fax bitonal, low DPI).

## Splits & the hidden test set
`manifest.jsonl` marks each page `train` or `test` (80/20). **Test-page answer
GT is withheld** (held by evaluators) — the test split over-samples the hard
cases (multi-page continuation, perspective, handwriting). Train GT is provided
so you can develop and self-check. The grader scores your pipeline on the hidden
test pages. Do not attempt to reconstruct withheld GT.

## Ground-truth formats (quick reference)
- **cells.json**: `{table_uid, cells:[{row_start,row_end,col_start,col_end,bbox,text,is_header,is_handwritten}]}`. `bbox = [x0,y0,x1,y1]` in image pixels.
- **tables.html**: a single `<table>` with real `rowspan`/`colspan` (grouped headers, category cells) — feed to TEDS.
- **hocr / alto.xml**: word-level boxes; handwriting carries `class="handwritten"` (hOCR) / `STYLE="handwritten"` (ALTO); redacted values carry `class="redacted"` and text `[REDACTED]`.
- **meta.json / manifest.jsonl**: `homography` (3×3), `orientation` (0/90/180), `size` `[w,h]`, `artifacts`, `overlays`, `has_handwriting`, `has_redaction` — useful for debugging/analysis, not needed for extraction. Note: `size` follows the rotated image for 90° pages.

## Metrics you'll be scored on
TEDS-Struct, GriTS-Top/Con, cell-F1 (structure); CER/WER reported separately for
print vs handwriting; multi-page stitch-reconstruction F1 (see `stitch.json`).
Run `eval/ocr_eval.py` against the train split to self-check.

Regenerate with `tools/docgen/generate.py` then `tools/docgen/split.py`.
