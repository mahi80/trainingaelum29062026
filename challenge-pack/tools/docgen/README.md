# docgen — synthetic scanned-document generator

Generates the `example/` corpus: 100 "scanned" auto-loan / banking page-images
that are deliberately hard for OCR, plus pixel-aligned ground truth.

## Why it exists
The take-home **provides** the labeled dataset (candidates don't create data).
This tool is shipped for transparency and reproducibility — you can inspect
exactly how the documents and labels were produced, and regenerate them.

## What it produces
- **`example/images/page_XXXX.jpg`** — grayscale "scans" (JPEG, ~A4) across **9 doc
  types** (credit application, bank/income verification, policy rate sheet, pay stub,
  W-2, dealer invoice, driver license, vehicle title, insurance card) with:
  - tables that **span multiple pages** (repeated headers + continuation rows),
  - **merged / spanning cells** — colspan group headers + rowspan category cells,
  - **skewed / rotated / perspective-distorted** columns; a few pages rotated **90°/180°**,
  - **dense multi-line** cells,
  - **cursive handwritten** field values, signatures, and margin notes,
  - **checkboxes**, semi-transparent **stamps + watermarks**, and **redaction bars**
    (redacted values → `[REDACTED]` token, `class="redacted"`),
  - scan **artifacts**: hole punches, staples, creases, coffee stains, photocopy/fax
    (bitonal), and variable DPI.
- **`example/gt/page_XXXX.*`** ground truth, all in *distorted* image coordinates:
  | File | Format | Used for |
  |------|--------|----------|
  | `.cells.json` | grid-matrix (row/col start/end, bbox, text, header, handwritten) | GriTS, cell-F1 |
  | `.tables.html` | PubTabNet-style `<table>` with rowspan/colspan | TEDS / TEDS-Struct |
  | `.hocr` | hOCR words w/ bbox + conf, `class=handwritten` | CER/WER |
  | `.alto.xml` | ALTO v4 | CER/WER (alt) |
  | `.meta.json` | seed, homography matrix, distortions, doc class | provenance / audit |
- **`example/manifest.jsonl`** — one row per page (doc_id, class, page_in_doc,
  table_uid, distortions, has_handwriting, split).
- **`example/stitch.json`** — multi-page table continuation links.
- **`example/SHA256SUMS`** — integrity of all public files.

## How alignment stays exact
Ground truth is captured in **clean** page coordinates as each token/cell is
drawn, then transformed by the **same homography** used to warp the image
(`tx_bbox(H, ...)`). No human OCR of our own synthetic data → labels are
machine-exact even after skew/perspective.

## Run it
```bash
pip install -r requirements.txt          # Pillow + numpy
python generate.py --out ../../example --seed 1337           # all 100 pages
python generate.py --out ../../example --seed 1337 --limit 6 # quick smoke
# then publish step (assigns 80/20 split, withholds TEST answers to grading-kit):
python split.py --example ../../example --hidden ../../../grading-kit/hidden --seed 1337
```

## Fonts
Resolves fonts from `tools/docgen/fonts/` (drop OFL fonts like Caveat /
DancingScript here for cross-platform reproducibility), then falls back to
system fonts (Windows: Arial/Times + Lucida Handwriting/Segoe Script/Brush
Script; Linux: DejaVu). The committed images were rendered with handwriting
fonts; only the *images* are distributed, not the font files.

## Determinism
A single `--seed` threads into every random choice (and `numpy.random`).
`split.py` uses a disjoint seed so train/test selection is independent.
Re-running `generate.py` regenerates ALL gt (including test) into `example/`;
re-run `split.py` afterward to re-withhold the test answers.
