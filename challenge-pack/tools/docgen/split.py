#!/usr/bin/env python3
"""Publish step: assign an 80/20 train/test split and withhold test answers.

`generate.py` writes ALL ground truth into `example/`. This script then:
  * scores each page by "hardness" (multi-page continuation, perspective,
    handwriting) and selects ~20% for the hidden TEST set, over-sampling hard
    pages so the test set stresses the hard cases;
  * stamps `split` into manifest.jsonl;
  * MOVES the answer GT for test pages (cells.json, tables.html, hocr, alto.xml)
    out of the public tree into `grading-kit/hidden/gt_test/`;
  * writes `grading-kit/hidden/test_pages.json`;
  * rewrites SHA256SUMS over the (now public) example tree.

Run after generate.py:
  python split.py --example ../../example --hidden ../../../grading-kit/hidden --frac 0.2 --seed 1337
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import random
import shutil

ANSWER_EXT = [".cells.json", ".tables.html", ".hocr", ".alto.xml"]  # withheld
PUBLIC_KEEP_EXT = [".meta.json"]  # stays public (no text answers, only provenance)


def hardness(row, jitter):
    s = 0.0
    if row.get("page_in_doc", 1) > 1:
        s += 2.0          # continuation page of a multi-page table
    if "perspective" in row.get("distortions", []):
        s += 1.5
    if row.get("has_handwriting"):
        s += 1.0
    return s + jitter


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--example", default="../../example")
    ap.add_argument("--hidden", default="../../../grading-kit/hidden")
    ap.add_argument("--frac", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=1337)
    args = ap.parse_args()

    rng = random.Random(args.seed + 7)  # disjoint from generation seed
    man_path = os.path.join(args.example, "manifest.jsonl")
    rows = [json.loads(ln) for ln in open(man_path, encoding="utf-8") if ln.strip()]

    n_test = max(1, round(len(rows) * args.frac))
    ranked = sorted(rows, key=lambda r: hardness(r, rng.random()), reverse=True)
    test_pages = {r["page"] for r in ranked[:n_test]}

    gt_dir = os.path.join(args.example, "gt")
    test_gt = os.path.join(args.hidden, "gt_test")
    os.makedirs(test_gt, exist_ok=True)

    moved = 0
    for r in rows:
        r["split"] = "test" if r["page"] in test_pages else "train"
        if r["split"] == "test":
            for ext in ANSWER_EXT:
                src = os.path.join(gt_dir, r["page"] + ext)
                if os.path.isfile(src):
                    shutil.move(src, os.path.join(test_gt, r["page"] + ext))
                    moved += 1

    with open(man_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    with open(os.path.join(args.hidden, "test_pages.json"), "w", encoding="utf-8") as f:
        json.dump(sorted(test_pages), f, indent=1)

    # rewrite public SHA256SUMS
    sums = []
    for root, _, files in os.walk(args.example):
        for fn in sorted(files):
            if fn == "SHA256SUMS":
                continue
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, args.example).replace(os.sep, "/")
            h = hashlib.sha256(open(p, "rb").read()).hexdigest()
            sums.append(f"{h}  {rel}")
    with open(os.path.join(args.example, "SHA256SUMS"), "w", encoding="utf-8") as f:
        f.write("\n".join(sums) + "\n")

    print(f"train={len(rows)-len(test_pages)} test={len(test_pages)} "
          f"answer-files moved={moved}")
    print(f"hidden test GT -> {test_gt}")


if __name__ == "__main__":
    main()
