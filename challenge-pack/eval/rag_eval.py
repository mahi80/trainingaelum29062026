"""Retrieval (RAG) evaluation CLI.

Computes Recall@40, MRR, and nDCG@10 for a retrieval run, and -- when the run
file carries both a ``pre_rerank`` and a ``post_rerank`` ranked list -- reports
the *lift* the reranker delivers on each metric.

File formats
------------
``--qrels`` JSON: relevance judgments, mapping each query id to its relevant doc
ids. Two accepted shapes:

    {"q1": ["doc_a", "doc_b"], ...}                       # binary relevance
    {"q1": {"doc_a": 2, "doc_b": 1}, ...}                 # graded relevance (gain)

``--run`` JSON: ranked retrieval results per query. Either a single ranking:

    {"q1": ["doc_x", "doc_a", ...], ...}                  # one ranking per query

or separate pre/post-rerank rankings to measure lift:

    {"q1": {"pre_rerank": ["doc_x", ...],
            "post_rerank": ["doc_a", ...]}, ...}

Doc ids are ranked best-first. Missing queries score zero.

Usage
-----
    python -m eval.rag_eval --qrels eval/qrels_sample.json --run run.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Dict, List, Optional

DEFAULT_QRELS = os.path.join(os.path.dirname(__file__), "qrels_sample.json")
RECALL_K = 40
NDCG_K = 10


# --------------------------------------------------------------------------- #
# Loading / normalisation
# --------------------------------------------------------------------------- #
def load_qrels(path: str) -> Dict[str, Dict[str, float]]:
    """Return {qid: {docid: gain}}. Binary lists become gain 1.0."""
    with open(path, "r", encoding="utf-8") as fh:
        obj = json.load(fh)
    out: Dict[str, Dict[str, float]] = {}
    for qid, rel in obj.items():
        if isinstance(rel, dict):
            out[str(qid)] = {str(d): float(g) for d, g in rel.items() if float(g) > 0}
        else:
            out[str(qid)] = {str(d): 1.0 for d in rel}
    return out


def load_run(path: str) -> Dict[str, Dict[str, List[str]]]:
    """Return {qid: {"pre_rerank": [...], "post_rerank": [...]}}.

    A single-ranking run is mapped to post_rerank only (pre_rerank empty), so the
    headline metrics still compute and lift is reported as None.
    """
    with open(path, "r", encoding="utf-8") as fh:
        obj = json.load(fh)
    out: Dict[str, Dict[str, List[str]]] = {}
    for qid, val in obj.items():
        if isinstance(val, dict):
            pre = [str(d) for d in val.get("pre_rerank", [])]
            post = [str(d) for d in val.get("post_rerank", val.get("ranking", []))]
            out[str(qid)] = {"pre_rerank": pre, "post_rerank": post}
        else:
            out[str(qid)] = {"pre_rerank": [], "post_rerank": [str(d) for d in val]}
    return out


# --------------------------------------------------------------------------- #
# Metrics (per query)
# --------------------------------------------------------------------------- #
def recall_at_k(ranking: List[str], rel: Dict[str, float], k: int) -> float:
    if not rel:
        return 0.0
    topk = ranking[:k]
    hits = sum(1 for d in topk if d in rel)
    return hits / len(rel)


def reciprocal_rank(ranking: List[str], rel: Dict[str, float]) -> float:
    for i, d in enumerate(ranking, start=1):
        if d in rel:
            return 1.0 / i
    return 0.0


def dcg_at_k(ranking: List[str], rel: Dict[str, float], k: int) -> float:
    dcg = 0.0
    for i, d in enumerate(ranking[:k], start=1):
        gain = rel.get(d, 0.0)
        if gain > 0:
            dcg += (2.0 ** gain - 1.0) / math.log2(i + 1)
    return dcg


def ndcg_at_k(ranking: List[str], rel: Dict[str, float], k: int) -> float:
    ideal_gains = sorted(rel.values(), reverse=True)[:k]
    idcg = 0.0
    for i, gain in enumerate(ideal_gains, start=1):
        idcg += (2.0 ** gain - 1.0) / math.log2(i + 1)
    if idcg == 0:
        return 0.0
    return dcg_at_k(ranking, rel, k) / idcg


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def _score_phase(
    qrels: Dict[str, Dict[str, float]],
    rankings: Dict[str, List[str]],
) -> Dict[str, float]:
    """Macro-average each metric over the queries present in qrels."""
    qids = list(qrels.keys())
    if not qids:
        return {"recall@40": 0.0, "mrr": 0.0, "ndcg@10": 0.0, "n_queries": 0}
    rec = mrr = ndcg = 0.0
    for qid in qids:
        rel = qrels[qid]
        ranking = rankings.get(qid, [])
        rec += recall_at_k(ranking, rel, RECALL_K)
        mrr += reciprocal_rank(ranking, rel)
        ndcg += ndcg_at_k(ranking, rel, NDCG_K)
    n = len(qids)
    return {
        "recall@40": round(rec / n, 4),
        "mrr": round(mrr / n, 4),
        "ndcg@10": round(ndcg / n, 4),
        "n_queries": n,
    }


def evaluate(
    qrels: Dict[str, Dict[str, float]],
    run: Dict[str, Dict[str, List[str]]],
) -> dict:
    pre = {qid: run.get(qid, {}).get("pre_rerank", []) for qid in qrels}
    post = {qid: run.get(qid, {}).get("post_rerank", []) for qid in qrels}

    has_pre = any(len(v) > 0 for v in pre.values())

    post_scores = _score_phase(qrels, post)
    result: Dict[str, object] = {"post_rerank": post_scores}

    if has_pre:
        pre_scores = _score_phase(qrels, pre)
        lift = {
            "recall@40": round(post_scores["recall@40"] - pre_scores["recall@40"], 4),
            "mrr": round(post_scores["mrr"] - pre_scores["mrr"], 4),
            "ndcg@10": round(post_scores["ndcg@10"] - pre_scores["ndcg@10"], 4),
        }
        result["pre_rerank"] = pre_scores
        result["rerank_lift"] = lift
    else:
        result["_note"] = (
            "Run had no pre_rerank lists; reporting post-rerank metrics only. "
            "Provide {pre_rerank, post_rerank} per query to measure reranker lift."
        )
    return result


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eval.rag_eval",
        description="Score retrieval runs (Recall@40, MRR, nDCG@10) with pre/post-rerank lift.",
    )
    p.add_argument(
        "--qrels",
        default=DEFAULT_QRELS,
        help="Relevance judgments JSON (default: eval/qrels_sample.json).",
    )
    p.add_argument("--run", required=True, help="Retrieval run JSON (rankings per query).")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not os.path.exists(args.qrels):
        print(json.dumps({"error": "qrels file not found: " + args.qrels}))
        return 2
    if not os.path.exists(args.run):
        print(json.dumps({"error": "run file not found: " + args.run}))
        return 2
    qrels = load_qrels(args.qrels)
    run = load_run(args.run)
    summary = evaluate(qrels, run)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
