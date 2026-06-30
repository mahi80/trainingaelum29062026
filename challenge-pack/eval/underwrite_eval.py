"""Underwriting / risk-model evaluation CLI.

Scores a probability-of-default model and its accept/decline decisions against
labels. All metric math is pure python (no numpy/sklearn required); sklearn is
used only if present and only to cross-check, never required.

Metrics
-------
  * AUC-ROC          -- area under the ROC curve, computed exactly from the
                        Mann-Whitney U statistic (rank-based, ties handled).
  * PR-AUC           -- average precision (area under the precision-recall curve)
                        via the step-wise sum over recall increments.
  * Brier score      -- mean squared error between pd_score and the binary label
                        (lower is better; well-calibrated models score low).
  * decision-agreement -- fraction of rows where the predicted ``decision`` matches
                        the label-implied decision. The label-implied decision is
                        ``approve`` when ``default == 0`` else ``decline``, unless
                        a ``decision`` column is present in the labels file.

File formats (CSV with headers)
-------------------------------
``--pred`` preds.csv columns:  ``pd_score`` (float in [0,1]) and ``decision``
   (one of approve/decline/refer; case-insensitive). An ``id`` column is optional
   and, if present in both files, is used to align rows; otherwise rows align by
   order.

``--labels`` labels.csv columns: ``default`` (1 = defaulted/bad, 0 = good). May
   also carry ``decision`` (the gold decision) and an optional ``id``.

Usage
-----
    python -m eval.underwrite_eval --pred preds.csv --labels labels.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import List, Optional, Tuple

APPROVE = "approve"
DECLINE = "decline"


# --------------------------------------------------------------------------- #
# CSV loading + alignment
# --------------------------------------------------------------------------- #
def _read_csv(path: str) -> List[dict]:
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _norm_decision(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    v = s.strip().lower()
    if v in ("approve", "approved", "accept", "accepted", "1", "true", "yes"):
        return APPROVE
    if v in ("decline", "declined", "reject", "rejected", "deny", "0", "false", "no"):
        return DECLINE
    if v in ("refer", "review", "manual"):
        return "refer"
    return v or None


def align(preds: List[dict], labels: List[dict]) -> Tuple[List[float], List[int], List[Optional[str]], List[Optional[str]]]:
    """Align preds and labels, by ``id`` when available else by row order.

    Returns parallel lists: ``(scores, y_true, pred_decisions, gold_decisions)``.
    """
    have_ids = (
        preds and labels and "id" in preds[0] and "id" in labels[0]
    )
    if have_ids:
        lab_by_id = {str(r["id"]): r for r in labels}
        rows = [(p, lab_by_id[str(p["id"])]) for p in preds if str(p.get("id")) in lab_by_id]
    else:
        rows = list(zip(preds, labels))

    scores: List[float] = []
    y_true: List[int] = []
    pred_dec: List[Optional[str]] = []
    gold_dec: List[Optional[str]] = []
    for p, label in rows:
        try:
            scores.append(float(p.get("pd_score", p.get("score", "nan"))))
        except (TypeError, ValueError):
            scores.append(float("nan"))
        try:
            y_true.append(int(float(label.get("default", label.get("label", 0)))))
        except (TypeError, ValueError):
            y_true.append(0)
        pred_dec.append(_norm_decision(p.get("decision")))
        gold_dec.append(_norm_decision(label.get("decision")))
    return scores, y_true, pred_dec, gold_dec


# --------------------------------------------------------------------------- #
# Metrics (pure python)
# --------------------------------------------------------------------------- #
def auc_roc(scores: List[float], y_true: List[int]) -> Optional[float]:
    """Exact AUC via the rank-sum (Mann-Whitney U), averaging ranks over ties."""
    pairs = [(s, y) for s, y in zip(scores, y_true) if s == s]  # drop NaN
    n_pos = sum(1 for _, y in pairs if y == 1)
    n_neg = sum(1 for _, y in pairs if y == 0)
    if n_pos == 0 or n_neg == 0:
        return None
    # Assign average (fractional) ranks, ascending by score.
    order = sorted(range(len(pairs)), key=lambda i: pairs[i][0])
    ranks = [0.0] * len(pairs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and pairs[order[j + 1]][0] == pairs[order[i]][0]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0  # ranks are 1-based
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    sum_ranks_pos = sum(ranks[idx] for idx, (_, y) in enumerate(pairs) if y == 1)
    auc = (sum_ranks_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)
    return round(auc, 4)


def pr_auc(scores: List[float], y_true: List[int]) -> Optional[float]:
    """Average Precision: sum of precision at each positive, over recall steps."""
    pairs = sorted(
        ((s, y) for s, y in zip(scores, y_true) if s == s),
        key=lambda t: t[0],
        reverse=True,
    )
    total_pos = sum(1 for _, y in pairs if y == 1)
    if total_pos == 0:
        return None
    tp = 0
    fp = 0
    ap = 0.0
    prev_recall = 0.0
    for _s, y in pairs:
        if y == 1:
            tp += 1
        else:
            fp += 1
        precision = tp / (tp + fp)
        recall = tp / total_pos
        ap += precision * (recall - prev_recall)
        prev_recall = recall
    return round(ap, 4)


def brier_score(scores: List[float], y_true: List[int]) -> Optional[float]:
    vals = [(s, y) for s, y in zip(scores, y_true) if s == s]
    if not vals:
        return None
    return round(sum((s - y) ** 2 for s, y in vals) / len(vals), 4)


def decision_agreement(
    pred_dec: List[Optional[str]],
    gold_dec: List[Optional[str]],
    y_true: List[int],
) -> Optional[float]:
    """Agreement between predicted decisions and gold decisions.

    Gold decision comes from the labels' ``decision`` column when present; else it
    is derived from the default label (default==0 -> approve, else decline). Only
    rows with a predicted decision are scored.
    """
    n = 0
    agree = 0
    for i, pd in enumerate(pred_dec):
        if pd is None:
            continue
        gd = gold_dec[i]
        if gd is None:
            gd = APPROVE if y_true[i] == 0 else DECLINE
        n += 1
        if pd == gd:
            agree += 1
    if n == 0:
        return None
    return round(agree / n, 4)


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def evaluate(pred_path: str, labels_path: str) -> dict:
    preds = _read_csv(pred_path)
    labels = _read_csv(labels_path)
    scores, y_true, pred_dec, gold_dec = align(preds, labels)
    n = len(scores)
    summary = {
        "n_rows": n,
        "n_positives": sum(1 for y in y_true if y == 1),
        "AUC_ROC": auc_roc(scores, y_true),
        "PR_AUC": pr_auc(scores, y_true),
        "Brier": brier_score(scores, y_true),
        "decision_agreement": decision_agreement(pred_dec, gold_dec, y_true),
    }
    if summary["AUC_ROC"] is None:
        summary["_warning"] = (
            "AUC/PR-AUC undefined: need both default==1 and default==0 rows."
        )
    return summary


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eval.underwrite_eval",
        description="Score underwriting PD/decision predictions (AUC-ROC, PR-AUC, Brier, agreement).",
    )
    p.add_argument("--pred", required=True, help="preds.csv with pd_score, decision columns.")
    p.add_argument("--labels", required=True, help="labels.csv with default (and optional decision).")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not os.path.exists(args.pred):
        print(json.dumps({"error": "pred file not found: " + args.pred}))
        return 2
    if not os.path.exists(args.labels):
        print(json.dumps({"error": "labels file not found: " + args.labels}))
        return 2
    summary = evaluate(args.pred, args.labels)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
