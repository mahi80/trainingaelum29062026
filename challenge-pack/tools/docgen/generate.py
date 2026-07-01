#!/usr/bin/env python3
"""AutoLoan-DocIntel synthetic scanned-document generator.

Produces `example/images/page_XXXX.jpg` ("scans") plus pixel-aligned ground
truth, for auto-loan / banking documents that are deliberately hard.

Variation covered
-----------------
* Document types: credit application, bank/income verification (multi-page),
  underwriting policy & rate sheet (multi-page), pay stub, W-2 tax form,
  dealer invoice, driver's license, vehicle title, insurance card.
* Table structure: multi-page tables (repeated headers), **merged/spanning
  cells** (colspan group headers + rowspan category cells).
* Handwriting: cursive field values, signatures, margin notes, strikethrough
  corrections.
* Overlays: checkboxes, semi-transparent stamps + watermarks, redaction bars
  (redacted values become a `[REDACTED]` token, class="redacted").
* Geometry: skew / rotation / perspective (recorded homography) + a few pages
  rotated 90/180 for orientation robustness (GT remapped).
* Scan artifacts: vignette, noise, blur, hole punches, staples, creases,
  coffee stains, photocopy/fax (bitonal), variable DPI.

Ground truth is captured in clean page coordinates as each token/cell is drawn,
then transformed by the SAME homography (and orientation) applied to the image,
so labels stay pixel-aligned. Deterministic: one --seed threads everywhere.

Usage:
  python generate.py --out ../../example --seed 1337
  python generate.py --out ../../example --seed 1337 --limit 8   # smoke
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
PAGE_W, PAGE_H = 1000, 1414
MARGIN = 70
PAPER = 247
INK = 25

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLED = os.path.join(HERE, "fonts")
WINFONTS = r"C:\Windows\Fonts"
LINUXFONTS = "/usr/share/fonts"

PRINT_FONTS = ["arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"]
BOLD_FONTS = ["arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"]
SERIF_FONTS = ["times.ttf", "DejaVuSerif.ttf", "LiberationSerif-Regular.ttf"]
MONO_FONTS = ["cour.ttf", "consola.ttf", "DejaVuSansMono.ttf"]
HAND_FONTS = ["Caveat-Regular.ttf", "DancingScript-Regular.ttf", "LHANDW.TTF",
              "FRSCRIPT.TTF", "segoesc.ttf", "Inkfree.ttf", "BRUSHSCI.TTF"]

DOC_CLASSES = {
    "application": "Auto Loan Credit Application",
    "verification": "Income / Bank Statement Verification",
    "policy": "Underwriting Policy & Rate Sheet",
    "paystub": "Payroll Earnings Statement",
    "w2": "Form W-2 Wage and Tax Statement",
    "dealer_invoice": "Dealer Vehicle Invoice",
    "driver_license": "Driver License",
    "vehicle_title": "Certificate of Vehicle Title",
    "insurance_card": "Auto Insurance ID Card",
}

HAND_NOTES = ["Approved", "Refer to UW", "Verify income", "Co-signer required",
              "DTI exception", "See note", "OK", "Declined", "Pending docs",
              "Stip cleared", "LTV high", "Manual review", "Rechecked"]
FIRST = ["James", "Maria", "Wei", "Aisha", "Carlos", "Priya", "John", "Sofia",
         "Liam", "Noor", "Diego", "Hana", "Omar", "Grace", "Ivan", "Lena"]
LAST = ["Okafor", "Nguyen", "Smith", "Patel", "Garcia", "Khan", "Brown",
        "Rossi", "Cohen", "Mensah", "Ito", "Silva", "Park", "Dubois"]
MAKES = ["Toyota", "Honda", "Tesla", "Ford", "Hyundai", "Kia", "Nissan", "BMW"]
MODELS = ["Corolla", "Civic", "Model 3", "F-150", "Elantra", "Sportage", "Leaf", "320i"]
FUEL = ["ICE", "Hybrid", "EV"]
COND = ["New", "Used", "CPO"]
BRANCHES = ["Pune", "Austin", "Leeds", "Berlin", "Toronto", "Dublin"]
STATES = ["TX", "CA", "NY", "IL", "GA", "WA", "OH", "FL"]
STAMPS = ["APPROVED", "RECEIVED", "COPY", "CONFIDENTIAL", "PAID", "VERIFIED", "ORIGINAL"]

# --------------------------------------------------------------------------- #
_font_cache: dict = {}


def _find_font(names):
    for n in names:
        for bdir in (BUNDLED, WINFONTS, LINUXFONTS):
            p = os.path.join(bdir, n)
            if os.path.isfile(p):
                return p
        if os.path.isdir(LINUXFONTS):
            for root, _, files in os.walk(LINUXFONTS):
                if n in files:
                    return os.path.join(root, n)
    return None


def font(kind, size):
    key = (kind, size)
    if key in _font_cache:
        return _font_cache[key]
    table = {"print": PRINT_FONTS, "bold": BOLD_FONTS,
             "serif": SERIF_FONTS, "mono": MONO_FONTS, "hand": HAND_FONTS}
    path = _find_font(table[kind])
    f = ImageFont.truetype(path, size) if path else ImageFont.load_default()
    _font_cache[key] = f
    return f


def hand_font(rng, size):
    avail = [n for n in HAND_FONTS if _find_font([n])]
    name = rng.choice(avail) if avail else None
    if not name:
        return font("print", size)
    key = ("hand:" + name, size)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(_find_font([name]), size)
    return _font_cache[key]


# --------------------------------------------------------------------------- #
# Homography (clean -> distorted) + orthogonal orientation
# --------------------------------------------------------------------------- #
def make_homography(rng, distortions):
    cx, cy = PAGE_W / 2, PAGE_H / 2
    theta = shx = gx = gy = 0.0
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


def warp_image(img, H):
    Hinv = np.linalg.inv(H)
    Hinv = Hinv / Hinv[2, 2]
    return img.transform((PAGE_W, PAGE_H), Image.PERSPECTIVE, Hinv.flatten()[:8].tolist(),
                         resample=Image.BICUBIC, fillcolor=PAPER)


def tx_bbox(H, b):
    x0, y0, x1, y1 = b
    pts = [(H @ np.array([x, y, 1.0])) for x, y in
           ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    xs = [p[0] / p[2] for p in pts]
    ys = [p[1] / p[2] for p in pts]
    return [int(round(min(xs))), int(round(min(ys))),
            int(round(max(xs))), int(round(max(ys)))]


def orient_bbox(b, deg, w, h):
    x0, y0, x1, y1 = b
    if deg == 180:
        return [w - x1, h - y1, w - x0, h - y0]
    if deg == 90:   # image rotated 90 clockwise (ROTATE_270); new size (h, w)
        return [h - y1, x0, h - y0, x1]
    return list(b)


# --------------------------------------------------------------------------- #
@dataclass
class Token:
    text: str
    bbox: tuple
    hand: bool = False
    redacted: bool = False


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
        cy = y
        line = []
        for w in text.split():
            if fnt.getlength(" ".join(line + [w])) > max_w and line:
                self.words(x, cy, " ".join(line), fnt, hand)
                line = [w]
                cy += line_h
            else:
                line.append(w)
        if line:
            self.words(x, cy, " ".join(line), fnt, hand)
            cy += line_h
        return cy

    def field(self, x, y, label, value, rng, hand=True, redact=False):
        self.words(x, y, label, font("print", 22))
        vx = x + 250
        if redact:
            vf = font("print", 22)
            w = int(vf.getlength(value)) + 12
            self.d.rectangle([vx, y - 2, vx + w, y + 26], fill=(20, 20, 20))
            self.tokens.append(Token("[REDACTED]", (vx, y - 2, vx + w, y + 26),
                                     redacted=True))
        else:
            vf = hand_font(rng, 30) if hand else font("print", 22)
            self.words(vx, y - (8 if hand else 0), value, vf, hand=hand)

    def checkbox(self, x, y, label, checked, fnt=None):
        fnt = fnt or font("print", 20)
        self.d.rectangle([x, y, x + 20, y + 20], outline=INK, width=2)
        if checked:
            self.d.line([x + 3, y + 10, x + 9, y + 17], fill=INK, width=2)
            self.d.line([x + 9, y + 17, x + 18, y + 2], fill=INK, width=2)
            self.tokens.append(Token("[x]", (x, y, x + 20, y + 20)))
        else:
            self.tokens.append(Token("[ ]", (x, y, x + 20, y + 20)))
        return self.words(x + 28, y, label, fnt)


# --------------------------------------------------------------------------- #
# Table drawing with colspan group headers + optional rowspan first column
# --------------------------------------------------------------------------- #
def draw_table(cv, x, y, col_w, header, data_rows, rng, *, groups=None,
               rowspan0=None, row_h=40, start_row_idx=0, hand_col=None,
               redact_col=None):
    cells = []
    table_w = sum(col_w)
    xoff = [x]
    for w in col_w:
        xoff.append(xoff[-1] + w)
    cy = y
    hrow = 0

    if groups:  # colspan super-header row
        cv.d.rectangle([x, cy, x + table_w, cy + row_h], outline=INK, width=2,
                       fill=(224, 224, 224))
        covered = set()
        for label, c0, c1 in groups:
            gx0, gx1 = xoff[c0], xoff[c1 + 1]
            cv.d.rectangle([gx0, cy, gx1, cy + row_h], outline=INK, width=1)
            cv.words(gx0 + 6, cy + 9, label, font("bold", 18))
            cells.append(Cell(0, 0, c0, c1, label, (gx0, cy, gx1, cy + row_h), header=True))
            covered |= set(range(c0, c1 + 1))
        for c in range(len(col_w)):
            if c not in covered:
                cv.d.rectangle([xoff[c], cy, xoff[c + 1], cy + row_h], outline=INK, width=1)
                cells.append(Cell(0, 0, c, c, "", (xoff[c], cy, xoff[c + 1], cy + row_h), header=True))
        cy += row_h
        hrow = 1

    # header row
    hf = font("bold", 20)
    cv.d.rectangle([x, cy, x + table_w, cy + row_h], outline=INK, width=2, fill=(232, 232, 232))
    for c, (w, txt) in enumerate(zip(col_w, header)):
        cv.words(xoff[c] + 6, cy + 9, txt, hf)
        cv.d.line([xoff[c], cy, xoff[c], cy + row_h], fill=INK, width=1)
        cells.append(Cell(hrow, hrow, c, c, txt, (xoff[c], cy, xoff[c] + w, cy + row_h), header=True))
    cv.d.line([x + table_w, cy, x + table_w, cy + row_h], fill=INK, width=1)
    cy += row_h

    data_top = cy
    bf = font("print", 19)
    for ri, row in enumerate(data_rows):
        multiline = any(len(str(v)) > 22 for v in row)
        rh = row_h * 2 if multiline else row_h
        cv.d.rectangle([x, cy, x + table_w, cy + rh], outline=INK, width=1)
        gr = hrow + 1 + start_row_idx + ri
        for c, (w, val) in enumerate(zip(col_w, row)):
            if rowspan0 and c == 0:
                cv.d.line([xoff[c], cy, xoff[c], cy + rh], fill=INK, width=1)
                continue  # col-0 drawn as merged cells afterwards
            is_hand = (hand_col is not None and c == hand_col)
            do_redact = (redact_col is not None and c == redact_col)
            cv.d.line([xoff[c], cy, xoff[c], cy + rh], fill=INK, width=1)
            if do_redact:
                cv.d.rectangle([xoff[c] + 4, cy + 6, xoff[c + 1] - 6, cy + rh - 6], fill=(20, 20, 20))
                cv.tokens.append(Token("[REDACTED]", (xoff[c] + 4, cy + 6, xoff[c + 1] - 6, cy + rh - 6), redacted=True))
                cells.append(Cell(gr, gr, c, c, "[REDACTED]", (xoff[c], cy, xoff[c + 1], cy + rh)))
            else:
                fnt = hand_font(rng, 26) if is_hand else bf
                if multiline:
                    cv.wrapped(xoff[c] + 6, cy + 6, str(val), fnt, w - 12, 22, hand=is_hand)
                else:
                    cv.words(xoff[c] + 6, cy + 10, str(val), fnt, hand=is_hand)
                cells.append(Cell(gr, gr, c, c, str(val), (xoff[c], cy, xoff[c + 1], cy + rh), hand=is_hand))
        cv.d.line([x + table_w, cy, x + table_w, cy + rh], fill=INK, width=1)
        cy += rh

    if rowspan0:  # merged first-column category cells spanning runs of rows
        ry = data_top
        gr = hrow + 1 + start_row_idx
        for label, n in rowspan0:
            top = ry
            bot = ry + row_h * n
            cv.d.rectangle([xoff[0], top, xoff[1], bot], outline=INK, width=1, fill=(240, 240, 240))
            cv.words(xoff[0] + 6, top + 8, label, font("bold", 18))
            cells.append(Cell(gr, gr + n - 1, 0, 0, label, (xoff[0], top, xoff[1], bot), header=False))
            ry = bot
            gr += n
    return cells, (x, y, x + table_w, cy)


# --------------------------------------------------------------------------- #
def header_block(cv, doc_class, doc_id, page_in_doc, total):
    cv.words(MARGIN, 36, "RegLoan Bank — " + DOC_CLASSES[doc_class], font("serif", 30))
    cv.d.line([MARGIN, 78, PAGE_W - MARGIN, 78], fill=INK, width=2)
    cv.words(MARGIN, 86, f"Document {doc_id}", font("print", 18))
    cv.words(PAGE_W - MARGIN - 200, 86, f"Page {page_in_doc} of {total}", font("print", 18))


def _sig(cv, rng, x, y, name):
    cv.words(x, y, "Authorized Signature:", font("print", 22))
    cv.words(x + 250, y - 6, name, hand_font(rng, 38), hand=True)
    cv.d.line([x + 240, y + 36, x + 620, y + 36], fill=INK, width=1)


def gen_application(cv, doc_id, rng):
    header_block(cv, "application", doc_id, 1, 1)
    y = 128
    fn, ln = rng.choice(FIRST), rng.choice(LAST)
    fields = [("Applicant Name:", f"{fn} {ln}", False),
              ("Date of Birth:", f"{rng.randint(1,28):02d}/{rng.randint(1,12):02d}/{rng.randint(1965,2002)}", False),
              ("SSN (tokenized):", f"TKN-{rng.randint(100000,999999)}", rng.random() < 0.5),
              ("Annual Income:", f"${rng.randint(35,180)*1000:,}", False),
              ("Loan Amount:", f"${rng.randint(8,60)*1000:,}", False),
              ("Branch:", rng.choice(BRANCHES), False)]
    for lab, val, red in fields:
        cv.field(MARGIN, y, lab, val, rng, hand=True, redact=red)
        y += 44
    # checkboxes
    cv.words(MARGIN, y, "Employment:", font("print", 22))
    cx = cv.checkbox(MARGIN + 150, y, "Full-time", rng.random() < 0.6)
    cv.checkbox(cx + 30, y, "Self-employed", rng.random() < 0.4)
    y += 44
    cv.words(MARGIN, y, "Housing:", font("print", 22))
    cx = cv.checkbox(MARGIN + 150, y, "Own", rng.random() < 0.5)
    cv.checkbox(cx + 30, y, "Rent", rng.random() < 0.5)
    y += 52
    cv.words(MARGIN, y, "Collateral (Vehicle)", font("bold", 22))
    y += 40
    header = ["VIN", "Make", "Model", "Year", "Fuel", "Cond", "Value ($)"]
    col_w = [180, 110, 120, 70, 90, 90, 120]
    rows = [[f"VIN{rng.randint(10000,99999)}AL", rng.choice(MAKES), rng.choice(MODELS),
             rng.randint(2016, 2025), rng.choice(FUEL), rng.choice(COND),
             f"{rng.randint(12,55)*1000:,}"] for _ in range(rng.randint(1, 2))]
    cells, tb = draw_table(cv, MARGIN, y, col_w, header, rows, rng, row_h=42)
    _sig(cv, rng, MARGIN, tb[3] + 24, f"{fn} {ln}")
    return cells, "T-" + doc_id + "-1"


def _txn_rows(rng, n):
    rows = []
    bal = rng.randint(2000, 9000)
    for _ in range(n):
        debit = rng.choice([0, rng.randint(20, 900)])
        credit = rng.choice([0, rng.randint(50, 2500)])
        bal += credit - debit
        desc = rng.choice(["POS PURCHASE GROCERY", "ACH PAYROLL DEPOSIT", "AUTO LOAN PMT",
                            "ATM WITHDRAWAL", "ONLINE TRANSFER TO SAVINGS ACCT", "UTILITY BILL"])
        rows.append([f"{rng.randint(1,28):02d}/{rng.randint(1,12):02d}/2025", desc,
                     f"{debit:,}" if debit else "-", f"{credit:,}" if credit else "-", f"{bal:,}"])
    return rows


def gen_verification(doc_id, n_pages, rng):
    header = ["Date", "Description", "Debit ($)", "Credit ($)", "Balance ($)"]
    col_w = [130, 360, 110, 110, 130]
    groups = [("Transaction", 2, 3), ("Running", 4, 4)]
    pages, frags = [], []
    rpp = 16
    grow = 0
    for p in range(n_pages):
        cv = Canvas()
        header_block(cv, "verification", doc_id, p + 1, n_pages)
        cv.words(MARGIN, 122, "Statement of Account" + (" — continued" if p else ""), font("bold", 22))
        data = _txn_rows(rng, rpp)
        cells, _ = draw_table(cv, MARGIN, 168, col_w, header, data, rng,
                              groups=groups, row_h=40, start_row_idx=grow)
        if rng.random() < 0.5:
            cv.words(PAGE_W - 230, 190 + rng.randint(0, 300), rng.choice(HAND_NOTES),
                     hand_font(rng, 30), hand=True)
        pages.append((cv, Page(doc_id, "verification", p + 1, cv.tokens, cells, "T-" + doc_id + "-1")))
        frags.append({"page_in_doc": p + 1, "frag_index": p, "is_continuation": p > 0,
                      "header_repeated": True, "first_row_global_idx": grow})
        grow += rpp
    return pages, {"logical_doc_id": doc_id, "table_uid": "T-" + doc_id + "-1", "fragments": frags}


def gen_policy(doc_id, n_pages, rng):
    fico = ["580-619", "620-659", "660-699", "700-739", "740-779", "780+"]
    tiers = ["<=80%", "81-90%", "91-100%", "101-110%", "111-120%"]
    header = ["FICO Band"] + [f"LTV {t}" for t in tiers]
    col_w = [150] + [150] * len(tiers)
    groups = [("Loan-to-Value tier APR", 1, len(tiers))]
    bpp = max(1, math.ceil(len(fico) / n_pages))
    pages, frags = [], []
    grow = 0
    for p in range(n_pages):
        cv = Canvas()
        header_block(cv, "policy", doc_id, p + 1, n_pages)
        cv.words(MARGIN, 122, "APR Rate Matrix by FICO x LTV" + (" (cont.)" if p else ""), font("bold", 22))
        chunk = fico[p * bpp:(p + 1) * bpp] or [fico[-1]]
        rows = [[b] + [f"{round(rng.uniform(3.5,13.5),2)}%" for _ in tiers] for b in chunk]
        cells, tb = draw_table(cv, MARGIN, 168, col_w, header, rows, rng,
                               groups=groups, row_h=44, start_row_idx=grow)
        if rng.random() < 0.6:
            cv.words(MARGIN, tb[3] + 26, rng.choice(HAND_NOTES) + " — UW", hand_font(rng, 30), hand=True)
        pages.append((cv, Page(doc_id, "policy", p + 1, cv.tokens, cells, "T-" + doc_id + "-1")))
        frags.append({"page_in_doc": p + 1, "frag_index": p, "is_continuation": p > 0,
                      "header_repeated": True, "first_row_global_idx": grow})
        grow += len(rows)
    return pages, {"logical_doc_id": doc_id, "table_uid": "T-" + doc_id + "-1", "fragments": frags}


def gen_paystub(cv, doc_id, rng):
    header_block(cv, "paystub", doc_id, 1, 1)
    fn, ln = rng.choice(FIRST), rng.choice(LAST)
    cv.field(MARGIN, 122, "Employee:", f"{fn} {ln}", rng, hand=False)
    cv.field(MARGIN, 160, "Pay Period:", f"{rng.randint(1,28):02d}/{rng.randint(1,6):02d}/2025", rng, hand=False)
    cv.field(MARGIN + 460, 122, "Emp. ID:", f"E{rng.randint(1000,9999)}", rng, hand=False)
    # rowspan category column + colspan (Current | YTD)
    header = ["Category", "Item", "Current ($)", "YTD ($)"]
    col_w = [130, 250, 140, 140]
    groups = [("Amounts", 2, 3)]
    earn = [["", "Regular", f"{rng.randint(1800,4200):,}", f"{rng.randint(20000,60000):,}"],
            ["", "Overtime", f"{rng.randint(0,900):,}", f"{rng.randint(0,9000):,}"],
            ["", "Bonus", f"{rng.randint(0,1500):,}", f"{rng.randint(0,15000):,}"]]
    ded = [["", "Federal Tax", f"{rng.randint(200,800):,}", f"{rng.randint(2000,9000):,}"],
           ["", "Health", f"{rng.randint(50,300):,}", f"{rng.randint(600,3600):,}"]]
    rows = earn + ded
    rowspan0 = [("Earnings", 3), ("Deductions", 2)]
    cells, tb = draw_table(cv, MARGIN, 210, col_w, header, rows, rng,
                           groups=groups, rowspan0=rowspan0, row_h=44)
    cv.field(MARGIN, tb[3] + 20, "Net Pay:", f"${rng.randint(1500,4000):,}", rng, hand=True)
    return cells, "T-" + doc_id + "-1"


def gen_w2(cv, doc_id, rng):
    header_block(cv, "w2", doc_id, 1, 1)
    fn, ln = rng.choice(FIRST), rng.choice(LAST)
    cv.field(MARGIN, 122, "Employee:", f"{fn} {ln}", rng, hand=False)
    cv.field(MARGIN, 160, "SSN:", f"TKN-{rng.randint(100000,999999)}", rng, hand=False, redact=rng.random() < 0.6)
    header = ["Box", "Description", "Amount ($)"]
    col_w = [90, 320, 160]
    boxes = [("1", "Wages, tips, other comp.", rng.randint(30000, 160000)),
             ("2", "Federal income tax withheld", rng.randint(3000, 30000)),
             ("3", "Social security wages", rng.randint(30000, 160000)),
             ("4", "Social security tax withheld", rng.randint(2000, 9000)),
             ("5", "Medicare wages and tips", rng.randint(30000, 160000)),
             ("6", "Medicare tax withheld", rng.randint(500, 3000))]
    rows = [[b, d, f"{a:,}"] for b, d, a in boxes]
    cells, _ = draw_table(cv, MARGIN, 210, col_w, header, rows, rng, row_h=42)
    return cells, "T-" + doc_id + "-1"


def gen_dealer_invoice(cv, doc_id, rng):
    header_block(cv, "dealer_invoice", doc_id, 1, 1)
    cv.field(MARGIN, 122, "Dealer:", rng.choice(["Metro Autos", "Sunrise Motors", "CityDrive"]), rng, hand=False)
    cv.field(MARGIN, 160, "VIN:", f"VIN{rng.randint(10000,99999)}AL", rng, hand=False)
    header = ["Line", "Description", "Qty", "Unit ($)", "Amount ($)"]
    col_w = [70, 300, 70, 130, 140]
    groups = [("Charges", 3, 4)]
    items = [("1", f"{rng.choice(MAKES)} {rng.choice(MODELS)} base", rng.randint(18000, 52000)),
             ("2", "Destination fee", rng.randint(800, 1500)),
             ("3", "Options package", rng.randint(500, 5000)),
             ("4", "Doc fee", rng.randint(85, 500)),
             ("5", "Tax", rng.randint(1200, 4200))]
    rows = [[i, d, 1, f"{a:,}", f"{a:,}"] for i, d, a in items]
    cells, tb = draw_table(cv, MARGIN, 210, col_w, header, rows, rng, groups=groups, row_h=42)
    cv.field(MARGIN, tb[3] + 20, "Total:", f"${sum(a for _,_,a in items):,}", rng, hand=True)
    return cells, "T-" + doc_id + "-1"


def gen_driver_license(cv, doc_id, rng):
    header_block(cv, "driver_license", doc_id, 1, 1)
    fn, ln = rng.choice(FIRST), rng.choice(LAST)
    cv.d.rectangle([MARGIN, 120, MARGIN + 760, 430], outline=INK, width=2)
    cv.d.rectangle([MARGIN + 20, 150, MARGIN + 180, 380], outline=INK, width=1)  # photo box
    cv.words(MARGIN + 55, 250, "PHOTO", font("print", 20))
    fields = [("DL No:", f"{rng.choice(STATES)}{rng.randint(1000000,9999999)}"),
              ("Name:", f"{fn} {ln}"), ("DOB:", f"{rng.randint(1,28):02d}/{rng.randint(1,12):02d}/{rng.randint(1960,2003)}"),
              ("Class:", rng.choice(["C", "D", "M"])), ("Exp:", f"{rng.randint(1,28):02d}/2030"),
              ("State:", rng.choice(STATES))]
    y = 160
    for lab, val in fields:
        cv.field(MARGIN + 210, y, lab, val, rng, hand=False)
        y += 42
    _sig(cv, rng, MARGIN, 460, f"{fn} {ln}")
    return [], None


def gen_vehicle_title(cv, doc_id, rng):
    header_block(cv, "vehicle_title", doc_id, 1, 1)
    fn, ln = rng.choice(FIRST), rng.choice(LAST)
    fields = [("VIN:", f"VIN{rng.randint(10000,99999)}AL"), ("Owner:", f"{fn} {ln}"),
              ("Make:", rng.choice(MAKES)), ("Model:", rng.choice(MODELS)),
              ("Year:", str(rng.randint(2015, 2025))), ("Odometer:", f"{rng.randint(0,120000):,} mi"),
              ("Lienholder:", "RegLoan Bank"), ("Title No:", f"T{rng.randint(100000,999999)}")]
    y = 130
    for lab, val in fields:
        cv.field(MARGIN, y, lab, val, rng, hand=False)
        y += 46
    _sig(cv, rng, MARGIN, y + 10, f"{fn} {ln}")
    return [], None


def gen_insurance_card(cv, doc_id, rng):
    header_block(cv, "insurance_card", doc_id, 1, 1)
    fn, ln = rng.choice(FIRST), rng.choice(LAST)
    cv.d.rectangle([MARGIN, 120, MARGIN + 600, 400], outline=INK, width=2)
    fields = [("Insurer:", rng.choice(["SafeDrive", "AutoShield", "MetroInsure"])),
              ("Policy No:", f"P{rng.randint(1000000,9999999)}"), ("Insured:", f"{fn} {ln}"),
              ("VIN:", f"VIN{rng.randint(10000,99999)}AL"),
              ("Coverage:", rng.choice(["Full", "Liability", "Comprehensive"])),
              ("Effective:", f"01/{rng.randint(1,12):02d}/2025"), ("Expires:", "12/2025")]
    y = 140
    for lab, val in fields:
        cv.field(MARGIN + 20, y, lab, val, rng, hand=False)
        y += 38
    return [], None


# --------------------------------------------------------------------------- #
# Post-warp scan artifacts + overlays (operate on the final image; no GT move)
# --------------------------------------------------------------------------- #
def degrade(img, rng):
    g = np.asarray(img.convert("L")).astype(np.float32)
    yy, xx = np.mgrid[0:PAGE_H, 0:PAGE_W]
    cx, cy = rng.uniform(0.3, 0.7) * PAGE_W, rng.uniform(0.3, 0.7) * PAGE_H
    rad = ((xx - cx) ** 2 + (yy - cy) ** 2) / (0.7 * (PAGE_W ** 2 + PAGE_H ** 2))
    g = g - rad * rng.uniform(10, 35) + np.random.normal(0, rng.uniform(3, 9), g.shape)
    out = Image.fromarray(np.clip(g, 0, 255).astype(np.uint8), "L")
    if rng.random() < 0.6:
        out = out.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 0.9)))
    return out


def artifacts(img, rng, flags):
    d = ImageDraw.Draw(img)
    if "holes" in flags:
        for i in range(rng.randint(2, 3)):
            yy = 200 + i * 480 + rng.randint(-30, 30)
            d.ellipse([28, yy, 60, yy + 32], fill=235, outline=120)
    if "staple" in flags:
        d.line([70, 60, 96, 66], fill=90, width=3)
    if "crease" in flags:
        cyl = rng.randint(400, 1000)
        d.line([0, cyl, PAGE_W, cyl + rng.randint(-8, 8)], fill=205, width=2)
    if "coffee" in flags:
        cx, cy = rng.randint(300, 800), rng.randint(300, 1100)
        r = rng.randint(60, 120)
        ov = Image.new("L", img.size, 0)
        od = ImageDraw.Draw(ov)
        od.ellipse([cx - r, cy - r, cx + r, cy + r], outline=90, width=8)
        od.ellipse([cx - r + 14, cy - r + 14, cx + r - 14, cy + r - 14], fill=30)
        img = Image.composite(Image.new("L", img.size, 150), img,
                              ov.point(lambda p: int(p * 0.35)))
    if "fax" in flags:  # photocopy / fax bitonal
        arr = np.asarray(img).astype(np.float32)
        arr += np.random.normal(0, 6, arr.shape)
        thr = np.where(arr > 150, 255, 0).astype(np.uint8)
        img = Image.fromarray(thr, "L")
    if "lowdpi" in flags:
        s = rng.uniform(0.5, 0.7)
        small = img.resize((int(PAGE_W * s), int(PAGE_H * s)), Image.BILINEAR)
        img = small.resize((PAGE_W, PAGE_H), Image.BILINEAR)
    return img


def add_watermark(img, rng):
    ov = Image.new("L", img.size, 0)
    txt = rng.choice(["CONFIDENTIAL", "COPY", "DRAFT", "ORIGINAL"])
    f = font("bold", 120)
    tmp = Image.new("L", (900, 200), 0)
    ImageDraw.Draw(tmp).text((10, 20), txt, font=f, fill=70)
    tmp = tmp.rotate(30, expand=True)
    ov.paste(tmp, (120, 500), tmp)
    return Image.composite(Image.new("L", img.size, 120), img, ov.point(lambda p: int(p * 0.5)))


def add_stamp(img, rng):
    txt = rng.choice(STAMPS)
    stamp = Image.new("L", (360, 130), 255)
    sd = ImageDraw.Draw(stamp)
    sd.rectangle([6, 6, 354, 124], outline=60, width=6)
    sd.text((26, 40), txt, font=font("bold", 46), fill=60)
    stamp = stamp.rotate(rng.uniform(-18, 12), expand=True, fillcolor=255)
    px, py = rng.randint(420, 560), rng.randint(120, 900)
    mask = stamp.point(lambda p: 90 if p < 200 else 0)
    img.paste(Image.new("L", stamp.size, 70), (px, py), mask)
    return img


def apply_orientation(img, toks, cells, deg):
    if deg == 0:
        return img, toks, cells, img.size
    w, h = img.size
    if deg == 180:
        img2 = img.transpose(Image.ROTATE_180)
    else:  # 90 clockwise
        img2 = img.transpose(Image.ROTATE_270)
    toks2 = [Token(t.text, tuple(orient_bbox(t.bbox, deg, w, h)), t.hand, t.redacted) for t in toks]
    cells2 = [Cell(c.r0, c.r1, c.c0, c.c1, c.text, tuple(orient_bbox(c.bbox, deg, w, h)), c.header, c.hand) for c in cells]
    return img2, toks2, cells2, img2.size


# --------------------------------------------------------------------------- #
def emit_hocr(tokens, size):
    w, h = size
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<html xmlns="http://www.w3.org/1999/xhtml"><head>',
           '<meta name="ocr-system" content="autoloan-docgen"/></head><body>',
           f'<div class="ocr_page" title="bbox 0 0 {w} {h}">']
    for i, t in enumerate(tokens):
        b = t.bbox
        cls = "handwritten" if t.hand else ("redacted" if t.redacted else "ocrx_word")
        conf = 60 if t.redacted else (88 if t.hand else 96)
        out.append(f'<span class="ocrx_word {cls}" id="w{i}" '
                   f'title="bbox {b[0]} {b[1]} {b[2]} {b[3]}; x_wconf {conf}">{html.escape(t.text)}</span>')
    out.append("</div></body></html>")
    return "\n".join(out)


def emit_alto(tokens, size):
    w, h = size
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#"><Layout>',
           f'<Page WIDTH="{w}" HEIGHT="{h}" PHYSICAL_IMG_NR="1"><PrintSpace>']
    for i, t in enumerate(tokens):
        b = t.bbox
        style = "handwritten" if t.hand else ("redacted" if t.redacted else "printed")
        out.append(f'<String ID="s{i}" CONTENT="{html.escape(t.text, quote=True)}" '
                   f'HPOS="{b[0]}" VPOS="{b[1]}" WIDTH="{b[2]-b[0]}" HEIGHT="{b[3]-b[1]}" STYLE="{style}"/>')
    out += ['</PrintSpace></Page></Layout></alto>']
    return "\n".join(out)


def emit_html_table(cells):
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


def emit_cells_json(uid, cells):
    return {"table_uid": uid, "cells": [
        {"row_start": c.r0, "row_end": c.r1, "col_start": c.c0, "col_end": c.c1,
         "text": c.text, "bbox": list(c.bbox), "is_header": c.header,
         "is_handwritten": c.hand} for c in cells]}


# --------------------------------------------------------------------------- #
def build_plan(rng):
    plan = []
    for i in range(1, 17):
        plan.append(("application", f"APP-{i:04d}", 1))
    for i, n in enumerate([4, 5, 4, 5, 3], 1):
        plan.append(("verification", f"VER-{i:04d}", n))
    for i, n in enumerate([3, 2, 3, 2, 3], 1):
        plan.append(("policy", f"POL-{i:04d}", n))
    singles = [("paystub", "PAY", 10), ("w2", "W2", 8), ("dealer_invoice", "INV", 8),
               ("driver_license", "DL", 8), ("vehicle_title", "TTL", 6), ("insurance_card", "INS", 10)]
    for kind, pfx, count in singles:
        for i in range(1, count + 1):
            plan.append((kind, f"{pfx}-{i:04d}", 1))
    return plan


SINGLE_GEN = {"application": gen_application, "paystub": gen_paystub, "w2": gen_w2,
              "dealer_invoice": gen_dealer_invoice, "driver_license": gen_driver_license,
              "vehicle_title": gen_vehicle_title, "insurance_card": gen_insurance_card}


def choose_distortions(rng):
    d = []
    if rng.random() < 0.75:
        d.append("rotate")
    if rng.random() < 0.55:
        d.append("skew")
    if rng.random() < 0.40:
        d.append("perspective")
    return d or ["rotate"]


def choose_artifacts(rng):
    pool = ["holes", "staple", "crease", "coffee", "fax", "lowdpi"]
    return [a for a in pool if rng.random() < (0.12 if a in ("fax", "lowdpi") else 0.22)]


def choose_orientation(rng):
    r = rng.random()
    return 180 if r < 0.06 else (90 if r < 0.10 else 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../../example")
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    np.random.seed(args.seed)
    img_dir = os.path.join(args.out, "images")
    gt_dir = os.path.join(args.out, "gt")
    os.makedirs(img_dir, exist_ok=True)
    os.makedirs(gt_dir, exist_ok=True)

    plan = build_plan(rng)
    manifest, stitches = [], []
    page_no = 0

    def render(cv, pg, distortions, uid):
        nonlocal page_no
        page_no += 1
        name = f"page_{page_no:04d}"
        img_name = name + ".jpg"
        H = make_homography(rng, distortions)
        warped = degrade(warp_image(cv.img, H), rng)
        art = choose_artifacts(rng)
        warped = artifacts(warped, rng, art)
        overlays = []
        if rng.random() < 0.28:
            warped = add_watermark(warped, rng)
            overlays.append("watermark")
        if rng.random() < 0.30:
            warped = add_stamp(warped, rng)
            overlays.append("stamp")
        toks = [Token(t.text, tx_bbox(H, t.bbox), t.hand, t.redacted) for t in cv.tokens]
        cells = [Cell(c.r0, c.r1, c.c0, c.c1, c.text, tx_bbox(H, c.bbox), c.header, c.hand) for c in pg.cells]
        deg = choose_orientation(rng)
        warped, toks, cells, size = apply_orientation(warped, toks, cells, deg)
        warped.save(os.path.join(img_dir, img_name), quality=68, optimize=True)
        for ext, data in ((".hocr", emit_hocr(toks, size)), (".alto.xml", emit_alto(toks, size)),
                          (".tables.html", emit_html_table(cells))):
            open(os.path.join(gt_dir, name + ext), "w", encoding="utf-8").write(data)
        json.dump(emit_cells_json(uid, cells), open(os.path.join(gt_dir, name + ".cells.json"), "w", encoding="utf-8"), indent=1)
        meta = {"page": name, "image": img_name, "seed": args.seed, "doc_id": pg.doc_id,
                "doc_class": pg.doc_class, "page_in_doc": pg.page_in_doc, "distortions": distortions,
                "artifacts": art, "overlays": overlays, "orientation": deg, "size": list(size),
                "homography": H.tolist(), "n_tokens": len(toks),
                "n_handwritten": sum(t.hand for t in toks), "n_redacted": sum(t.redacted for t in toks),
                "n_cells": len(cells)}
        json.dump(meta, open(os.path.join(gt_dir, name + ".meta.json"), "w", encoding="utf-8"), indent=1)
        manifest.append({"page_no": page_no, "page": name, "image": img_name, "doc_id": pg.doc_id,
                         "doc_class": pg.doc_class, "page_in_doc": pg.page_in_doc, "table_uid": uid,
                         "distortions": distortions, "artifacts": art, "overlays": overlays,
                         "orientation": deg, "has_handwriting": bool(meta["n_handwritten"]),
                         "has_redaction": bool(meta["n_redacted"])})

    for kind, doc_id, n in plan:
        if args.limit and page_no >= args.limit:
            break
        if kind in SINGLE_GEN:
            cv = Canvas()
            cells, uid = SINGLE_GEN[kind](cv, doc_id, rng)
            render(cv, Page(doc_id, kind, 1, cv.tokens, cells, uid), choose_distortions(rng), uid)
        else:
            pages, stitch = (gen_verification if kind == "verification" else gen_policy)(doc_id, n, rng)
            stitches.append(stitch)
            d = choose_distortions(rng)
            for cv, pg in pages:
                if args.limit and page_no >= args.limit:
                    break
                render(cv, pg, d, pg.table_uid)

    with open(os.path.join(args.out, "manifest.jsonl"), "w", encoding="utf-8") as f:
        for row in manifest:
            f.write(json.dumps(row) + "\n")
    json.dump(stitches, open(os.path.join(args.out, "stitch.json"), "w", encoding="utf-8"), indent=1)

    sums = []
    for root, _, files in os.walk(args.out):
        for fn in sorted(files):
            if fn == "SHA256SUMS":
                continue
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, args.out).replace(os.sep, "/")
            sums.append(f"{hashlib.sha256(open(p,'rb').read()).hexdigest()}  {rel}")
    open(os.path.join(args.out, "SHA256SUMS"), "w", encoding="utf-8").write("\n".join(sums) + "\n")

    print(f"generated {page_no} pages -> {img_dir}")
    from collections import Counter
    print("by class:", dict(Counter(m["doc_class"] for m in manifest)))
    print(f"stitched docs: {len(stitches)}  redacted pages: {sum(m['has_redaction'] for m in manifest)}")


if __name__ == "__main__":
    main()
