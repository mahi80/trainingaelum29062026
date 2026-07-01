"""OCR / table-structure recognition evaluation CLI.

Computes the four metric families the grading kit scores:

  * TEDS / TEDS-Struct  -- tree-edit-distance similarity over the HTML table tree
                           (``tables.html``). TEDS-Struct nulls out cell text so
                           only the grid topology is scored.
  * GriTS-Top / GriTS-Con -- grid-cell matching from ``cells.json``. Top(ology)
                           uses spatial bbox IoU between the matched grids;
                           Con(tent) uses an LCS over cell text. Reported as the
                           2D-LCS-style F-score used in the GriTS paper.
  * cell-F1             -- precision/recall/F1 over (row, col, text) cell tuples.
  * CER / WER          -- character/word error rate, reported *separately* for
                           printed vs handwritten tokens using the hOCR
                           ``class="... handwritten"`` annotation.

Prediction / GT layout
-----------------------
Both ``--pred`` and ``--gt`` are directories of per-page files named
``page_XXXX.<ext>``. For each page the evaluator looks for, and gracefully
skips when absent:

    page_XXXX.tables.html   -> TEDS / TEDS-Struct
    page_XXXX.cells.json    -> GriTS / cell-F1
    page_XXXX.hocr          -> CER / WER (print vs handwritten)

GT defaults to ``example/gt`` (the public TRAIN labels). Point ``--pred`` at your
own model output written in the *same* file formats.

Usage
-----
    python -m eval.ocr_eval --pred runs/ocr_out --gt example/gt
    python -m eval.ocr_eval --pred runs/ocr_out            # gt defaults to example/gt
    python -m eval.ocr_eval --pred runs/ocr_out --pages page_0001 page_0002

Heavy deps (``lxml``) are imported lazily; if unavailable the HTML-based TEDS
metrics are skipped with a warning and the rest still run.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from typing import Dict, List, Optional, Sequence, Tuple

try:
    from . import levenshtein as lev          # python -m eval.ocr_eval
except ImportError:                            # python eval/ocr_eval.py
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import levenshtein as lev

DEFAULT_GT = os.path.join("example", "gt")
PAGE_RE = re.compile(r"(page_\d+)")


# --------------------------------------------------------------------------- #
# Small generic helpers
# --------------------------------------------------------------------------- #
def _page_key(path: str) -> Optional[str]:
    m = PAGE_RE.search(os.path.basename(path))
    return m.group(1) if m else None


def _discover_pages(pred_dir: str, gt_dir: str) -> List[str]:
    """Pages present in BOTH dirs (by any recognised extension), sorted."""
    def keys(d: str) -> set:
        out = set()
        for ext in ("*.tables.html", "*.cells.json", "*.hocr"):
            for p in glob.glob(os.path.join(d, ext)):
                k = _page_key(p)
                if k:
                    out.add(k)
        return out

    common = keys(pred_dir) & keys(gt_dir)
    return sorted(common)


def _read(path: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------------- #
# TEDS  (Tree-Edit-Distance-based Similarity over HTML tables)
# --------------------------------------------------------------------------- #
class _TreeNode:
    __slots__ = ("tag", "text", "children")

    def __init__(self, tag: str, text: str = ""):
        self.tag = tag
        self.text = text
        self.children = []  # type: List[_TreeNode]


def _html_to_tree(html: str, ignore_text: bool):
    """Parse a table HTML fragment into a normalised tree.

    Uses lxml when available. Returns ``None`` if lxml is missing so the caller
    can skip TEDS cleanly. Only structural tags (table/thead/tbody/tr/td/th) are
    retained; colspan/rowspan are folded into the node tag so topology differences
    are penalised.
    """
    try:
        import lxml.html  # lazy / optional
    except Exception:  # pragma: no cover - environment without lxml
        return None

    try:
        root_el = lxml.html.fromstring(html)
    except Exception:
        return None

    structural = {"table", "thead", "tbody", "tr", "td", "th"}

    def build(el) -> Optional[_TreeNode]:
        tag = (el.tag or "").lower() if isinstance(el.tag, str) else ""
        if tag not in structural:
            return None
        label = tag
        if tag in ("td", "th"):
            cs = el.get("colspan", "1")
            rs = el.get("rowspan", "1")
            label = "{}[{}x{}]".format(tag, rs, cs)
        node = _TreeNode(label)
        if tag in ("td", "th") and not ignore_text:
            node.text = _norm_text(el.text_content())
        for child in el:
            sub = build(child)
            if sub is not None:
                node.children.append(sub)
        return node

    tables = root_el.xpath("//table")
    if not tables:
        if root_el.tag == "table":
            tables = [root_el]
        else:
            return _TreeNode("table")
    return build(tables[0]) or _TreeNode("table")


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _tree_size(node: Optional[_TreeNode]) -> int:
    if node is None:
        return 0
    return 1 + sum(_tree_size(c) for c in node.children)


def _node_cost(a: _TreeNode, b: _TreeNode) -> float:
    """Relabel cost in [0,1]: tag must match; text contributes normalised CER."""
    if a.tag != b.tag:
        return 1.0
    if not a.text and not b.text:
        return 0.0
    return lev.normalized_levenshtein(a.text, b.text)


def _tree_edit_distance(a: Optional[_TreeNode], b: Optional[_TreeNode]) -> float:
    """Zhang-Shasha-style ordered-forest edit distance (recursive, memoised).

    Inputs are small table trees (tens of nodes), so a simple recursive
    forest-distance over child sequences is fast enough and avoids a heavy dep.
    Costs: insert/delete a node = 1.0; relabel = ``_node_cost`` in [0,1].
    """
    if a is None and b is None:
        return 0.0
    if a is None:
        return float(_tree_size(b))
    if b is None:
        return float(_tree_size(a))

    memo: Dict[Tuple[int, ...], float] = {}

    def forest_dist(fa: List[_TreeNode], fb: List[_TreeNode]) -> float:
        key = (id_tuple(fa), id_tuple(fb))
        if key in memo:
            return memo[key]
        if not fa and not fb:
            res = 0.0
        elif not fa:
            res = sum(_tree_size(t) for t in fb)
        elif not fb:
            res = sum(_tree_size(t) for t in fa)
        else:
            a_last, b_last = fa[-1], fb[-1]
            # delete a_last
            del_a = forest_dist(fa[:-1] + a_last.children, fb) + 1.0
            # insert b_last
            ins_b = forest_dist(fa, fb[:-1] + b_last.children) + 1.0
            # match/relabel the two roots, recurse into their subforests
            sub = (
                forest_dist(a_last.children, b_last.children)
                + forest_dist(fa[:-1], fb[:-1])
                + _node_cost(a_last, b_last)
            )
            res = min(del_a, ins_b, sub)
        memo[key] = res
        return res

    # stable identity keys for memoisation (positions are unique per call)
    _ids: Dict[int, int] = {}

    def id_tuple(forest: List[_TreeNode]) -> Tuple[int, ...]:
        out = []
        for n in forest:
            if id(n) not in _ids:
                _ids[id(n)] = len(_ids)
            out.append(_ids[id(n)])
        return tuple(out)

    return forest_dist([a], [b])


def teds_score(pred_html: str, gt_html: str, ignore_text: bool) -> Optional[float]:
    """TEDS = 1 - TED(pred, gt) / max(|pred|, |gt|). Returns None if lxml missing."""
    pt = _html_to_tree(pred_html, ignore_text)
    gt = _html_to_tree(gt_html, ignore_text)
    if pt is None or gt is None:
        return None
    denom = max(_tree_size(pt), _tree_size(gt))
    if denom == 0:
        return 1.0
    dist = _tree_edit_distance(pt, gt)
    return max(0.0, 1.0 - dist / denom)


# --------------------------------------------------------------------------- #
# cells.json parsing  +  GriTS  +  cell-F1
# --------------------------------------------------------------------------- #
def _load_cells(path: str) -> List[dict]:
    raw = _read(path)
    if raw is None:
        return []
    obj = json.loads(raw)
    cells = obj.get("cells", obj) if isinstance(obj, dict) else obj
    return cells or []


def _cell_span(c: dict) -> Tuple[int, int, int, int]:
    return (
        int(c.get("row_start", 0)),
        int(c.get("row_end", c.get("row_start", 0))),
        int(c.get("col_start", 0)),
        int(c.get("col_end", c.get("col_start", 0))),
    )


def _bbox_iou(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) < 4 or len(b) < 4:
        return 0.0
    ax0, ay0, ax1, ay1 = a[0], a[1], a[2], a[3]
    bx0, by0, bx1, by1 = b[0], b[1], b[2], b[3]
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _grid_index(cells: List[dict]) -> Dict[Tuple[int, int], dict]:
    """Map every covered (row, col) location to its originating cell."""
    grid: Dict[Tuple[int, int], dict] = {}
    for c in cells:
        r0, r1, c0, c1 = _cell_span(c)
        for r in range(r0, r1 + 1):
            for col in range(c0, c1 + 1):
                grid.setdefault((r, col), c)
    return grid


def grits(pred_cells: List[dict], gt_cells: List[dict]) -> Tuple[float, float]:
    """Return ``(grits_top, grits_con)`` as F-scores in [0,1].

    Cells are matched by their grid location (row, col). For every shared
    location we accumulate:
      * topology agreement = bbox IoU of the two cells at that location,
      * content agreement  = char-LCS(pred_text, gt_text)/max_len.
    The F-score normalises the summed agreement by predicted and GT location
    counts (the GriTS 2D-LCS factored form, here approximated location-wise).
    """
    pred_grid = _grid_index(pred_cells)
    gt_grid = _grid_index(gt_cells)
    n_pred = len(pred_grid)
    n_gt = len(gt_grid)
    if n_pred == 0 and n_gt == 0:
        return 1.0, 1.0
    shared = set(pred_grid) & set(gt_grid)

    top_acc = 0.0
    con_acc = 0.0
    for loc in shared:
        pc, gc = pred_grid[loc], gt_grid[loc]
        top_acc += _bbox_iou(pc.get("bbox"), gc.get("bbox"))
        pt = _norm_text(pc.get("text", ""))
        gtxt = _norm_text(gc.get("text", ""))
        denom = max(len(pt), len(gtxt))
        con_acc += 1.0 if denom == 0 else lev.lcs_length(pt, gtxt) / denom

    def fscore(acc: float) -> float:
        if n_pred == 0 or n_gt == 0:
            return 0.0
        prec = acc / n_pred
        rec = acc / n_gt
        return 0.0 if (prec + rec) == 0 else 2 * prec * rec / (prec + rec)

    return fscore(top_acc), fscore(con_acc)


def cell_f1(pred_cells: List[dict], gt_cells: List[dict]) -> Tuple[float, float, float]:
    """Precision/recall/F1 over (row_start, col_start, normalised_text) tuples."""
    def keyset(cells: List[dict]):
        from collections import Counter
        return Counter(
            (int(c.get("row_start", 0)), int(c.get("col_start", 0)), _norm_text(c.get("text", "")))
            for c in cells
        )

    pred = keyset(pred_cells)
    gt = keyset(gt_cells)
    tp = sum((pred & gt).values())
    n_pred = sum(pred.values())
    n_gt = sum(gt.values())
    if n_pred == 0 and n_gt == 0:          # no table to find, none found -> perfect
        return 1.0, 1.0, 1.0
    prec = tp / n_pred if n_pred else 0.0
    rec = tp / n_gt if n_gt else 0.0
    f1 = 0.0 if (prec + rec) == 0 else 2 * prec * rec / (prec + rec)
    return prec, rec, f1


# --------------------------------------------------------------------------- #
# hOCR parsing  ->  printed vs handwritten token streams for CER/WER
# --------------------------------------------------------------------------- #
_WORD_RE = re.compile(
    r'<span[^>]*class="([^"]*)"[^>]*>(.*?)</span>', re.IGNORECASE | re.DOTALL
)
_TAG_RE = re.compile(r"<[^>]+>")


def _unescape(s: str) -> str:
    return (
        s.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&apos;", "'")
    )


def parse_hocr_words(hocr: str) -> Tuple[List[str], List[str]]:
    """Return ``(printed_words, handwritten_words)`` in document order.

    A word is handwritten iff its hOCR span ``class`` contains ``handwritten``.
    """
    printed: List[str] = []
    handwritten: List[str] = []
    for cls, inner in _WORD_RE.findall(hocr or ""):
        text = _unescape(_TAG_RE.sub("", inner)).strip()
        if not text:
            continue
        if "handwritten" in cls.lower():
            handwritten.append(text)
        else:
            printed.append(text)
    return printed, handwritten


# --------------------------------------------------------------------------- #
# Per-page + corpus driver
# --------------------------------------------------------------------------- #
def evaluate(pred_dir: str, gt_dir: str, pages: Optional[List[str]] = None) -> dict:
    if pages:
        page_keys = sorted(set(pages))
    else:
        page_keys = _discover_pages(pred_dir, gt_dir)

    teds_vals: List[float] = []
    teds_struct_vals: List[float] = []
    grits_top_vals: List[float] = []
    grits_con_vals: List[float] = []
    cellf1_p: List[float] = []
    cellf1_r: List[float] = []
    cellf1_f: List[float] = []

    print_pairs: List[Tuple[str, str]] = []
    hand_pairs: List[Tuple[str, str]] = []

    lxml_missing = False
    n_html = n_cells = n_hocr = 0

    for pk in page_keys:
        # --- TEDS from tables.html ---
        gt_html = _read(os.path.join(gt_dir, pk + ".tables.html"))
        pr_html = _read(os.path.join(pred_dir, pk + ".tables.html"))
        if gt_html is not None and pr_html is not None:
            t_full = teds_score(pr_html, gt_html, ignore_text=False)
            t_struct = teds_score(pr_html, gt_html, ignore_text=True)
            if t_full is None or t_struct is None:
                lxml_missing = True
            else:
                teds_vals.append(t_full)
                teds_struct_vals.append(t_struct)
                n_html += 1

        # --- GriTS + cell-F1 from cells.json ---
        gt_cells_path = os.path.join(gt_dir, pk + ".cells.json")
        pr_cells_path = os.path.join(pred_dir, pk + ".cells.json")
        if os.path.exists(gt_cells_path) and os.path.exists(pr_cells_path):
            gtc = _load_cells(gt_cells_path)
            prc = _load_cells(pr_cells_path)
            gt_top, gt_con = grits(prc, gtc)
            grits_top_vals.append(gt_top)
            grits_con_vals.append(gt_con)
            p, r, f = cell_f1(prc, gtc)
            cellf1_p.append(p)
            cellf1_r.append(r)
            cellf1_f.append(f)
            n_cells += 1

        # --- CER/WER from hocr (print vs handwritten) ---
        gt_hocr = _read(os.path.join(gt_dir, pk + ".hocr"))
        pr_hocr = _read(os.path.join(pred_dir, pk + ".hocr"))
        if gt_hocr is not None and pr_hocr is not None:
            gp, gh = parse_hocr_words(gt_hocr)
            pp, ph = parse_hocr_words(pr_hocr)
            print_pairs.append((" ".join(gp), " ".join(pp)))
            hand_pairs.append((" ".join(gh), " ".join(ph)))
            n_hocr += 1

    def avg(xs: List[float]) -> Optional[float]:
        return round(sum(xs) / len(xs), 4) if xs else None

    print_cer, _, _ = lev.corpus_cer(print_pairs)
    print_wer, _, _ = lev.corpus_wer(print_pairs)
    hand_cer, _, _ = lev.corpus_cer(hand_pairs)
    hand_wer, _, _ = lev.corpus_wer(hand_pairs)

    summary = {
        "pages_scored": len(page_keys),
        "counts": {"tables_html": n_html, "cells_json": n_cells, "hocr": n_hocr},
        "TEDS": avg(teds_vals),
        "TEDS_Struct": avg(teds_struct_vals),
        "GriTS_Top": avg(grits_top_vals),
        "GriTS_Con": avg(grits_con_vals),
        "cell_F1": avg(cellf1_f),
        "cell_precision": avg(cellf1_p),
        "cell_recall": avg(cellf1_r),
        "CER_printed": round(print_cer, 4) if print_pairs else None,
        "WER_printed": round(print_wer, 4) if print_pairs else None,
        "CER_handwritten": round(hand_cer, 4) if hand_pairs else None,
        "WER_handwritten": round(hand_wer, 4) if hand_pairs else None,
    }
    if lxml_missing:
        summary["_warnings"] = [
            "lxml not installed: TEDS / TEDS-Struct skipped. `pip install lxml`."
        ]
    return summary


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eval.ocr_eval",
        description="Score OCR/table-structure predictions (TEDS, GriTS, cell-F1, CER/WER).",
    )
    p.add_argument("--pred", required=True, help="Directory of predicted page_XXXX.* files.")
    p.add_argument(
        "--gt",
        default=DEFAULT_GT,
        help="Ground-truth directory (default: example/gt, the public TRAIN labels).",
    )
    p.add_argument(
        "--pages",
        nargs="*",
        default=None,
        help="Optional subset of page keys, e.g. page_0001 page_0002.",
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not os.path.isdir(args.pred):
        print(json.dumps({"error": "pred dir not found: " + args.pred}))
        return 2
    if not os.path.isdir(args.gt):
        print(json.dumps({"error": "gt dir not found: " + args.gt}))
        return 2
    summary = evaluate(args.pred, args.gt, args.pages)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
