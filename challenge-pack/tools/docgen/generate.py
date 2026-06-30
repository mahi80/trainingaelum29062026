#!/usr/bin/env python3
"""AutoLoan-DocIntel synthetic scanned-document generator.

Produces `example/images/page_XXXX.png` (grayscale "scans") plus aligned
ground truth, for auto-loan / banking documents that are deliberately hard:
multi-page tables, skewed/rotated/perspective-distorted columns, dense
multi-line cells, and cursive handwritten entries.

Design notes
------------
* Cross-platform: renders with Pillow (no WeasyPrint/Poppler/GTK needed).
* Ground truth is captured in *clean* page coordinates as each token/cell is
  drawn, then transformed by the SAME homography used to warp the image, so
  labels stay pixel-aligned after distortion. This is what makes scoring exact.
* Deterministic: a single --seed threads into every random choice.

Outputs (train split shipped publicly; test split GT withheld by split.py):
  images/page_XXXX.png
  gt/page_XXXX.cells.json     grid-matrix table GT (rows/cols/spans/bbox/text)
  gt/page_XXXX.tables.html    PubTabNet-style HTML (for TEDS)
  gt/page_XXXX.hocr           hOCR words (bbox + conf, class=handwritten)
  gt/page_XXXX.alto.xml       ALTO XML
  gt/page_XXXX.meta.json      seed, homography, distortions, doc class
  manifest.jsonl              one row per page
  stitch.json                 multi-page table continuation links
  SHA256SUMS                  integrity of all generated public files

Usage:
  python generate.py --out ../../example --seed 1337           # full 100 pages
  python generate.py --out ../../example --seed 1337 --limit 6 # quick smoke
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import random
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
PAGE_W, PAGE_H = 1000, 1414          # ~A4 aspect at modest DPI (small PNGs)
MARGIN = 70
PAPER = 247                          # background gray of a scanned page
INK = 25

# Font candidates: bundled OFL dir first, then common system paths.
HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLED = os.path.join(HERE, "fonts")
WINFONTS = r"C:\Windows\Fonts"
LINUXFONTS = "/usr/share/fonts"

PRINT_FONTS = ["arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"]
BOLD_FONTS = ["arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"]
SERIF_FONTS = ["times.ttf", "DejaVuSerif.ttf", "LiberationSerif-Regular.ttf"]
# cursive / handwriting pool (system on Windows; OFL Caveat/DancingScript if bundled)
HAND_FONTS = ["Caveat-Regular.ttf", "DancingScript-Regular.ttf",
              "LHANDW.TTF", "FRSCRIPT.TTF", "segoesc.ttf", "Inkfree.ttf", "BRUSHSCI.TTF"]

DOC_CLASSES = {"application": "Auto Loan Credit Application",
               "verification": "Income / Bank Statement Verification",
               "policy": "Underwriting Policy & Rate Sheet"}

# Controlled handwriting vocabulary (bounded label space -> exact GT)
HAND_NOTES = ["Approved", "Refer to UW", "Verify income", "Co-signer required",
              "DTI exception", "See note", "OK", "Declined", "Pending docs",
              "Stip cleared", "LTV high", "Manual review"]
FIRST = ["James", "Maria", "Wei", "Aisha", "Carlos", "Priya", "John", "Sofia",
         "Liam", "Noor", "Diego", "Hana", "Omar", "Grace", "Ivan", "Lena"]
LAST = ["Okafor", "Nguyen", "Smith", "Patel", "Garcia", "Khan", "Brown",
        "Rossi", "Cohen", "Mensah", "Ito", "Silva", "Park", "Dubois"]
MAKES = ["Toyota", "Honda", "Tesla", "Ford", "Hyundai", "Kia", "Nissan", "BMW"]
MODELS = ["Corolla", "Civic", "Model 3", "F-150", "Elantra", "Sportage", "Leaf", "320i"]
FUEL = ["ICE", "Hybrid", "EV"]
COND = ["New", "Used", "CPO"]
BRANCHES = ["Pune", "Austin", "Leeds", "Berlin", "Toronto", "Dublin"]


# --------------------------------------------------------------------------- #
# Fonts
# --------------------------------------------------------------------------- #
_font_cache: dict = {}


def _find_font(names):
    for n in names:
        for base in (BUNDLED, WINFONTS, LINUXFONTS):
            p = os.path.join(base, n)
            if os.path.isfile(p):
                return p
        # recursive search under linux fonts
        if os.path.isdir(LINUXFONTS):
            for root, _, files in os.walk(LINUXFONTS):
                if n in files:
                    return os.path.join(root, n)
    return None


def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    key = (kind, size)
    if key in _font_cache:
        return _font_cache[key]
    table = {"print": PRINT_FONTS, "bold": BOLD_FONTS,
             "serif": SERIF_FONTS, "hand": HAND_FONTS}
    path = _find_font(table[kind])
    f = ImageFont.truetype(path, size) if path else ImageFont.load_default()
    _font_cache[key] = f
    return f


def hand_font_for(rng: random.Random, size: int) -> ImageFont.FreeTypeFont:
    """Pick a random available cursive font so handwriting varies per page."""
    avail = [n for n in HAND_FONTS if _find_font([n])]
    name = rng.choice(avail) if avail else None
    if not name:
        return font("print", size)
    key = ("hand:" + name, size)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(_find_font([name]), size)
    return _font_cache[key]


# --------------------------------------------------------------------------- #
# Homography (clean -> distorted) and helpers
# --------------------------------------------------------------------------- #
def make_homography(rng: random.Random, distortions: list[str]) -> np.ndarray:
    cx, cy = PAGE_W / 2, PAGE_H / 2
    theta = 0.0
    shx = 0.0
    gx = gy = 0.0
    if "rotate" in distortions:
        theta = math.radians(rng.uniform(-6, 6))
    if "skew" in distortions:
        shx = rng.uniform(-0.12, 0.12)
    if "perspective" in distortions:
        gx = rng.uniform(-1.6e-4, 1.6e-4)
        gy = rng.uniform(-1.6e-4, 1.6e-4)
    T1 = np.array([[1, 0, -cx], [0, 1, -cy], [0, 0, 1]], float)
    R = np.array([[math.cos(theta), -math.sin(theta), 0],
                  [math.sin(theta), math.cos(theta), 0], [0, 0, 1]], float)
    Sh = np.array([[1, shx, 0], [0, 1, 0], [0, 0, 1]], float)
    P = np.array([[1, 0, 0], [0, 1, 0], [gx, gy, 1]], float)
    T2 = np.array([[1, 0, cx], [0, 1, cy], [0, 0, 1]], float)
    return T2 @ P @ Sh @ R @ T1


def warp_image(img: Image.Image, H: np.ndarray) -> Image.Image:
    Hinv = np.linalg.inv(H)
    Hinv = Hinv / Hinv[2, 2]
    coeffs = Hinv.flatten()[:8].tolist()
    return img.transform((PAGE_W, PAGE_H), Image.PERSPECTIVE, coeffs,
                         resample=Image.BICUBIC, fillcolor=PAPER)


def tx_point(H: np.ndarray, x: float, y: float) -> tuple[float, float]:
    v = H @ np.array([x, y, 1.0])
    return v[0] / v[2], v[1] / v[2]


def tx_bbox(H: np.ndarray, b: tuple[int, int, int, int]) -> list[int]:
    x0, y0, x1, y1 = b
    pts = [tx_point(H, x, y) for x, y in
           ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return [int(round(min(xs))), int(round(min(ys))),
            int(round(max(xs))), int(round(max(ys)))]


# --------------------------------------------------------------------------- #
# Canvas: draws and records word-level tokens + table cells in clean coords
# --------------------------------------------------------------------------- #
@dataclass
class Token:
    text: str
    bbox: tuple
    hand: bool = False


@dataclass
class Cell:
    r0: int
    r1: int
    c0: int
    c1: int
    text: str
    bbox: tuple
    header: bool = False
    hand: bool = False


@dataclass
class Page:
    doc_id: str
    doc_class: str
    page_in_doc: int
    tokens: list = field(default_factory=list)
    cells: list = field(default_factory=list)
    table_uid: str | None = None


class Canvas:
    def __init__(self):
        self.img = Image.new("RGB", (PAGE_W, PAGE_H), (PAPER, PAPER, PAPER))
        self.d = ImageDraw.Draw(self.img)
        self.tokens: list[Token] = []

    def words(self, x, y, text, fnt, hand=False, fill=INK):
        """Draw a run of words on one line; record per-word bboxes."""
        cx = x
        space = fnt.getlength(" ")
        for w in text.split(" "):
            if not w:
                cx += space
                continue
            self.d.text((cx, y), w, font=fnt, fill=fill)
            b = self.d.textbbox((cx, y), w, font=fnt)
            self.tokens.append(Token(w, b, hand))
            cx = b[2] + space
        return cx

    def wrapped(self, x, y, text, fnt, max_w, line_h, hand=False):
        """Greedy word-wrap inside max_w; returns bottom y."""
        words = text.split()
        line = []
        cy = y
        for w in words:
            trial = " ".join(line + [w])
            if fnt.getlength(trial) > max_w and line:
                self.words(x, cy, " ".join(line), fnt, hand)
                line = [w]
                cy += line_h
            else:
                line.append(w)
        if line:
            self.words(x, cy, " ".join(line), fnt, hand)
            cy += line_h
        return cy

    def label_value(self, x, y, label, value, hand=True):
        lf = font("print", 22)
        self.words(x, y, label, lf)
        vf = hand_font_for(_RNG, 30) if hand else font("print", 22)
        self.words(x + 230, y - (8 if hand else 0), value, vf, hand=hand)


# --------------------------------------------------------------------------- #
# Table drawing -> returns list[Cell] in clean coords
# --------------------------------------------------------------------------- #
def draw_table(cv: Canvas, x, y, col_w, header, data_rows, rng,
               hand_col=None, row_h=40, start_row_idx=0):
    cells: list[Cell] = []
    hf = font("bold", 20)
    bf = font("print", 19)
    table_w = sum(col_w)
    # header
    cy = y
    cv.d.rectangle([x, cy, x + table_w, cy + row_h], outline=INK, width=2,
                   fill=(232, 232, 232))
    cx = x
    for c, (w, txt) in enumerate(zip(col_w, header)):
        cv.words(cx + 6, cy + 9, txt, hf)
        cells.append(Cell(0, 0, c, c, txt, (cx, cy, cx + w, cy + row_h),
                          header=True))
        cx += w
    # vertical lines for header
    cx = x
    for w in col_w:
        cv.d.line([cx, cy, cx, cy + row_h], fill=INK, width=1)
        cx += w
    cv.d.line([x + table_w, cy, x + table_w, cy + row_h], fill=INK, width=1)
    cy += row_h
    # data rows (support 2-line dense cells)
    for ri, row in enumerate(data_rows):
        rh = row_h
        multiline = any(len(str(v)) > 22 for v in row)
        if multiline:
            rh = row_h * 2
        cv.d.rectangle([x, cy, x + table_w, cy + rh], outline=INK, width=1)
        cx = x
        for c, (w, val) in enumerate(zip(col_w, row)):
            is_hand = (hand_col is not None and c == hand_col)
            fnt = hand_font_for(rng, 26) if is_hand else bf
            if multiline:
                cv.wrapped(cx + 6, cy + 6, str(val), fnt, w - 12, 22, hand=is_hand)
            else:
                cv.words(cx + 6, cy + 10, str(val), fnt, hand=is_hand)
            cells.append(Cell(start_row_idx + ri + 1, start_row_idx + ri + 1,
                              c, c, str(val), (cx, cy, cx + w, cy + rh),
                              hand=is_hand))
            cv.d.line([cx, cy, cx, cy + rh], fill=INK, width=1)
            cx += w
        cv.d.line([x + table_w, cy, x + table_w, cy + rh], fill=INK, width=1)
        cy += rh
    return cells, (x, y, x + table_w, cy)


# --------------------------------------------------------------------------- #
# Document templates
# --------------------------------------------------------------------------- #
def header_block(cv: Canvas, doc_class: str, doc_id: str, page_in_doc: int,
                 total_pages: int):
    cv.words(MARGIN, 36, "RegLoan Bank — " + DOC_CLASSES[doc_class],
             font("serif", 30))
    cv.d.line([MARGIN, 78, PAGE_W - MARGIN, 78], fill=INK, width=2)
    cv.words(MARGIN, 86, f"Document {doc_id}", font("print", 18))
    cv.words(PAGE_W - MARGIN - 200, 86,
             f"Page {page_in_doc} of {total_pages}", font("print", 18))


def gen_application(cv: Canvas, doc_id, rng) -> tuple[list, str | None]:
    header_block(cv, "application", doc_id, 1, 1)
    y = 140
    fn, ln = rng.choice(FIRST), rng.choice(LAST)
    fields = [
        ("Applicant Name:", f"{fn} {ln}"),
        ("Date of Birth:", f"{rng.randint(1,28):02d}/{rng.randint(1,12):02d}/{rng.randint(1965,2002)}"),
        ("SSN (tokenized):", f"TKN-{rng.randint(100000,999999)}"),
        ("Annual Income:", f"${rng.randint(35,180)*1000:,}"),
        ("Employer:", rng.choice(["Acme Logistics", "Globex", "Initech", "Umbrella Co"])),
        ("Loan Amount:", f"${rng.randint(8,60)*1000:,}"),
        ("Term (months):", str(rng.choice([36, 48, 60, 72]))),
        ("Branch:", rng.choice(BRANCHES)),
    ]
    for lab, val in fields:
        cv.label_value(MARGIN, y, lab, val, hand=True)
        y += 46
    # vehicle / collateral table
    cv.words(MARGIN, y + 6, "Collateral (Vehicle)", font("bold", 22))
    y += 44
    header = ["VIN", "Make", "Model", "Year", "Fuel", "Cond", "Value ($)"]
    col_w = [180, 110, 120, 70, 90, 90, 120]
    rows = []
    for _ in range(rng.randint(1, 2)):
        rows.append([f"VIN{rng.randint(10000,99999)}AL", rng.choice(MAKES),
                     rng.choice(MODELS), rng.randint(2016, 2025),
                     rng.choice(FUEL), rng.choice(COND),
                     f"{rng.randint(12,55)*1000:,}"])
    cells, _ = draw_table(cv, MARGIN, y, col_w, header, rows, rng, row_h=42)
    # signature (handwritten)
    sy = y + 60 + 42 * len(rows)
    cv.words(MARGIN, sy, "Authorized Signature:", font("print", 22))
    cv.words(MARGIN + 250, sy - 6, f"{fn} {ln}", hand_font_for(rng, 38), hand=True)
    cv.d.line([MARGIN + 240, sy + 36, MARGIN + 620, sy + 36], fill=INK, width=1)
    return cells, "T-" + doc_id + "-1"


def _txn_rows(rng, n):
    rows = []
    bal = rng.randint(2000, 9000)
    for i in range(n):
        debit = rng.choice([0, rng.randint(20, 900)])
        credit = rng.choice([0, rng.randint(50, 2500)])
        bal += credit - debit
        desc = rng.choice(["POS PURCHASE GROCERY", "ACH PAYROLL DEPOSIT",
                            "AUTO LOAN PMT", "ATM WITHDRAWAL",
                            "ONLINE TRANSFER TO SAVINGS ACCT", "UTILITY BILL"])
        rows.append([f"{rng.randint(1,28):02d}/{rng.randint(1,12):02d}/2025",
                     desc, f"{debit:,}" if debit else "-",
                     f"{credit:,}" if credit else "-", f"{bal:,}"])
    return rows


def gen_verification(doc_id, n_pages, rng):
    """Long bank-statement table split across pages (header repeated)."""
    header = ["Date", "Description", "Debit ($)", "Credit ($)", "Balance ($)"]
    col_w = [130, 360, 110, 110, 130]
    pages = []
    frags = []
    rows_per_page = 18
    global_row = 0
    for p in range(n_pages):
        cv = Canvas()
        header_block(cv, "verification", doc_id, p + 1, n_pages)
        cv.words(MARGIN, 130, "Statement of Account — continued"
                 if p > 0 else "Statement of Account", font("bold", 22))
        data = _txn_rows(rng, rows_per_page)
        cells, _ = draw_table(cv, MARGIN, 174, col_w, header, data, rng,
                              hand_col=None, row_h=40, start_row_idx=global_row)
        # occasional handwritten margin note
        if rng.random() < 0.5:
            cv.words(PAGE_W - 230, 200 + rng.randint(0, 300),
                     rng.choice(HAND_NOTES), hand_font_for(rng, 30), hand=True)
        pg = Page(doc_id, "verification", p + 1, cv.tokens, cells,
                  "T-" + doc_id + "-1")
        pages.append((cv, pg))
        frags.append({"page_in_doc": p + 1, "frag_index": p,
                      "is_continuation": p > 0, "header_repeated": True,
                      "first_row_global_idx": global_row})
        global_row += rows_per_page
    stitch = {"logical_doc_id": doc_id, "table_uid": "T-" + doc_id + "-1",
              "fragments": frags}
    return pages, stitch


def gen_policy(doc_id, n_pages, rng):
    """LTV/DTI rate matrix; may span pages by FICO band ranges."""
    fico_bands = ["580-619", "620-659", "660-699", "700-739", "740-779", "780+"]
    ltv_tiers = ["<=80%", "81-90%", "91-100%", "101-110%", "111-120%"]
    header = ["FICO Band"] + [f"LTV {t}" for t in ltv_tiers]
    col_w = [150] + [150] * len(ltv_tiers)
    bands_per_page = max(1, math.ceil(len(fico_bands) / n_pages))
    pages = []
    frags = []
    global_row = 0
    for p in range(n_pages):
        cv = Canvas()
        header_block(cv, "policy", doc_id, p + 1, n_pages)
        cv.words(MARGIN, 130, "APR Rate Matrix by FICO × LTV"
                 + (" (cont.)" if p > 0 else ""), font("bold", 22))
        chunk = fico_bands[p * bands_per_page:(p + 1) * bands_per_page]
        if not chunk:
            chunk = [fico_bands[-1]]
        rows = []
        for band in chunk:
            base = 4.0 + (fico_bands.index(band) and 0)
            rows.append([band] + [f"{round(rng.uniform(3.5, 13.5),2)}%"
                                  for _ in ltv_tiers])
        cells, tb = draw_table(cv, MARGIN, 174, col_w, header, rows, rng,
                              row_h=44, start_row_idx=global_row)
        if rng.random() < 0.6:
            cv.words(MARGIN, tb[3] + 30, rng.choice(HAND_NOTES) + " — UW",
                     hand_font_for(rng, 30), hand=True)
        pg = Page(doc_id, "policy", p + 1, cv.tokens, cells,
                  "T-" + doc_id + "-1")
        pages.append((cv, pg))
        frags.append({"page_in_doc": p + 1, "frag_index": p,
                      "is_continuation": p > 0, "header_repeated": True,
                      "first_row_global_idx": global_row})
        global_row += len(rows)
    stitch = {"logical_doc_id": doc_id, "table_uid": "T-" + doc_id + "-1",
              "fragments": frags}
    return pages, stitch


# --------------------------------------------------------------------------- #
# Photometric degradation (does not move points)
# --------------------------------------------------------------------------- #
def degrade(img: Image.Image, rng: random.Random) -> Image.Image:
    g = img.convert("L")
    arr = np.asarray(g).astype(np.float32)
    # vignette / uneven illumination
    yy, xx = np.mgrid[0:PAGE_H, 0:PAGE_W]
    cx, cy = rng.uniform(0.3, 0.7) * PAGE_W, rng.uniform(0.3, 0.7) * PAGE_H
    rad = ((xx - cx) ** 2 + (yy - cy) ** 2) / (0.7 * (PAGE_W ** 2 + PAGE_H ** 2))
    arr = arr - rad * rng.uniform(10, 35)
    # gaussian noise
    arr = arr + np.random.normal(0, rng.uniform(3, 9), arr.shape)
    arr = np.clip(arr, 0, 255).astype(np.uint8)
    out = Image.fromarray(arr, "L")
    if rng.random() < 0.6:
        out = out.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 0.9)))
    return out


# --------------------------------------------------------------------------- #
# Ground-truth emitters (bboxes already transformed to distorted space)
# --------------------------------------------------------------------------- #
def emit_hocr(page_name, tokens, size) -> str:
    w, h = size
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<html xmlns="http://www.w3.org/1999/xhtml"><head>',
             '<meta name="ocr-system" content="autoloan-docgen"/>',
             '<meta name="ocr-capabilities" content="ocr_page ocrx_word"/>',
             '</head><body>',
             f'<div class="ocr_page" title="bbox 0 0 {w} {h}">']
    for i, t in enumerate(tokens):
        b = t.bbox
        cls = "handwritten" if t.hand else "ocrx_word"
        conf = 88 if t.hand else 96
        lines.append(
            f'<span class="ocrx_word {cls}" id="w{i}" '
            f'title="bbox {b[0]} {b[1]} {b[2]} {b[3]}; x_wconf {conf}">'
            f'{html.escape(t.text)}</span>')
    lines.append("</div></body></html>")
    return "\n".join(lines)


def emit_alto(page_name, tokens, size) -> str:
    w, h = size
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">',
           '<Description><MeasurementUnit>pixel</MeasurementUnit></Description>',
           '<Layout>', f'<Page WIDTH="{w}" HEIGHT="{h}" PHYSICAL_IMG_NR="1">',
           '<PrintSpace>']
    for i, t in enumerate(tokens):
        b = t.bbox
        tag = "handwritten" if t.hand else "printed"
        out.append(
            f'<String ID="s{i}" CONTENT="{html.escape(t.text, quote=True)}" '
            f'HPOS="{b[0]}" VPOS="{b[1]}" WIDTH="{b[2]-b[0]}" '
            f'HEIGHT="{b[3]-b[1]}" STYLE="{tag}"/>')
    out += ['</PrintSpace>', '</Page>', '</Layout>', '</alto>']
    return "\n".join(out)


def emit_html_table(cells) -> str:
    if not cells:
        return "<table></table>"
    max_r = max(c.r1 for c in cells)
    rows = []
    for r in range(max_r + 1):
        tds = []
        for c in sorted([c for c in cells if c.r0 == r], key=lambda z: z.c0):
            tag = "th" if c.header else "td"
            rs = f' rowspan="{c.r1-c.r0+1}"' if c.r1 > c.r0 else ""
            cs = f' colspan="{c.c1-c.c0+1}"' if c.c1 > c.c0 else ""
            tds.append(f"<{tag}{rs}{cs}>{html.escape(c.text)}</{tag}>")
        rows.append("<tr>" + "".join(tds) + "</tr>")
    return "<table>" + "".join(rows) + "</table>"


def emit_cells_json(table_uid, cells) -> dict:
    return {"table_uid": table_uid,
            "cells": [{"row_start": c.r0, "row_end": c.r1,
                       "col_start": c.c0, "col_end": c.c1,
                       "text": c.text, "bbox": list(c.bbox),
                       "is_header": c.header, "is_handwritten": c.hand}
                      for c in cells]}


# --------------------------------------------------------------------------- #
# Build plan: exactly 100 pages
# --------------------------------------------------------------------------- #
def build_plan(rng):
    plan = []  # (kind, doc_id, n_pages)
    for i in range(1, 41):                      # 40 single-page applications
        plan.append(("application", f"APP-{i:04d}", 1))
    ver_pages = [4, 4, 5, 3, 4, 5, 3, 2]        # 8 docs -> 30 pages
    for i, n in enumerate(ver_pages, 1):
        plan.append(("verification", f"VER-{i:04d}", n))
    pol_pages = [2, 2, 3, 2, 3, 2, 2, 3, 2, 3, 3, 3]   # 12 docs -> 30 pages
    for i, n in enumerate(pol_pages, 1):
        plan.append(("policy", f"POL-{i:04d}", n))
    return plan


_RNG = random.Random(0)  # replaced in main with seeded instance


def choose_distortions(rng):
    d = []
    if rng.random() < 0.75:
        d.append("rotate")
    if rng.random() < 0.55:
        d.append("skew")
    if rng.random() < 0.40:
        d.append("perspective")
    return d or ["rotate"]


def main():
    global _RNG
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../../example")
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--limit", type=int, default=0, help="cap pages (smoke test)")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    _RNG = rng
    np.random.seed(args.seed)

    img_dir = os.path.join(args.out, "images")
    gt_dir = os.path.join(args.out, "gt")
    os.makedirs(img_dir, exist_ok=True)
    os.makedirs(gt_dir, exist_ok=True)

    plan = build_plan(rng)
    manifest = []
    stitches = []
    page_no = 0

    def render(cv: Canvas, pg: Page, distortions, table_uid):
        nonlocal page_no
        page_no += 1
        name = f"page_{page_no:04d}"
        img_name = name + ".jpg"
        H = make_homography(rng, distortions)
        warped = warp_image(cv.img, H)
        warped = degrade(warped, rng)
        warped.save(os.path.join(img_dir, img_name), quality=68, optimize=True)
        # transform GT bboxes by H
        toks = [Token(t.text, tx_bbox(H, t.bbox), t.hand) for t in cv.tokens]
        cells = [Cell(c.r0, c.r1, c.c0, c.c1, c.text, tx_bbox(H, c.bbox),
                      c.header, c.hand) for c in pg.cells]
        size = (PAGE_W, PAGE_H)
        with open(os.path.join(gt_dir, name + ".hocr"), "w", encoding="utf-8") as f:
            f.write(emit_hocr(name, toks, size))
        with open(os.path.join(gt_dir, name + ".alto.xml"), "w", encoding="utf-8") as f:
            f.write(emit_alto(name, toks, size))
        with open(os.path.join(gt_dir, name + ".tables.html"), "w", encoding="utf-8") as f:
            f.write(emit_html_table(cells))
        with open(os.path.join(gt_dir, name + ".cells.json"), "w", encoding="utf-8") as f:
            json.dump(emit_cells_json(table_uid, cells), f, indent=1)
        meta = {"page": name, "image": img_name, "seed": args.seed,
                "doc_id": pg.doc_id, "doc_class": pg.doc_class,
                "page_in_doc": pg.page_in_doc, "distortions": distortions,
                "homography": H.tolist(), "n_tokens": len(toks),
                "n_handwritten": sum(t.hand for t in toks), "n_cells": len(cells)}
        with open(os.path.join(gt_dir, name + ".meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=1)
        manifest.append({"page_no": page_no, "page": name, "image": img_name,
                         "doc_id": pg.doc_id, "doc_class": pg.doc_class,
                         "page_in_doc": pg.page_in_doc, "table_uid": table_uid,
                         "distortions": distortions,
                         "has_handwriting": bool(meta["n_handwritten"])})

    for kind, doc_id, n in plan:
        if args.limit and page_no >= args.limit:
            break
        if kind == "application":
            cv = Canvas()
            cells, tuid = gen_application(cv, doc_id, rng)
            pg = Page(doc_id, "application", 1, cv.tokens, cells, tuid)
            render(cv, pg, choose_distortions(rng), tuid)
        elif kind == "verification":
            pages, stitch = gen_verification(doc_id, n, rng)
            stitches.append(stitch)
            d = choose_distortions(rng)
            for cv, pg in pages:
                if args.limit and page_no >= args.limit:
                    break
                render(cv, pg, d, pg.table_uid)
        else:
            pages, stitch = gen_policy(doc_id, n, rng)
            stitches.append(stitch)
            d = choose_distortions(rng)
            for cv, pg in pages:
                if args.limit and page_no >= args.limit:
                    break
                render(cv, pg, d, pg.table_uid)

    with open(os.path.join(args.out, "manifest.jsonl"), "w", encoding="utf-8") as f:
        for row in manifest:
            f.write(json.dumps(row) + "\n")
    with open(os.path.join(args.out, "stitch.json"), "w", encoding="utf-8") as f:
        json.dump(stitches, f, indent=1)

    # SHA256SUMS over all generated public files
    sums = []
    for root, _, files in os.walk(args.out):
        for fn in sorted(files):
            if fn == "SHA256SUMS":
                continue
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, args.out).replace(os.sep, "/")
            h = hashlib.sha256(open(p, "rb").read()).hexdigest()
            sums.append(f"{h}  {rel}")
    with open(os.path.join(args.out, "SHA256SUMS"), "w", encoding="utf-8") as f:
        f.write("\n".join(sums) + "\n")

    print(f"generated {page_no} pages -> {img_dir}")
    print(f"manifest rows: {len(manifest)}  stitched docs: {len(stitches)}")


if __name__ == "__main__":
    main()
