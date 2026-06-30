#!/usr/bin/env python3
"""AutoLoan-DocIntel automated grader.

Orchestrates the auto-gradable portion of the 100-point rubric for ONE submission
and writes ``grading-kit/scorecard.json``. It:

  1. runs the conformance pytest suite (schema scale, required tables/cols/FKs,
     manifest integrity, SHA256SUMS, leakage) and maps pass/fail -> points;
  2. runs the candidate eval CLIs under challenge-pack/eval/ (OCR, NL-to-SQL, RAG,
     underwriting) either by import or subprocess, reading the JSON metrics they
     print, and converts each metric to points via a threshold ladder;
  3. runs the live auth/session pytest if APP_BASE_URL is set;
  4. emits one scorecard entry per rubric line:
        {key, auto|manual, raw, points_awarded, max_points, notes}
     plus a total and a pass/fail flag (pass = 70).

Design rule: **degrade gracefully**. A missing submission part scores 0 for the
lines it would have earned — never a crash. Manual lines are recorded with
points_awarded=null so a human fills them in.

Heavy deps (pytest, the eval CLIs' libs) are imported lazily / invoked as
subprocesses, so this module imports cleanly without them.

Usage:
    python grading-kit/harness/scorer.py
    python grading-kit/harness/scorer.py --submission /path/to/repo --out /path/scorecard.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

# --------------------------------------------------------------------------- #
# repo location (mirror conftest, but standalone so scorer runs without pytest)
# --------------------------------------------------------------------------- #
HERE = Path(__file__).resolve()


def find_repo_root(start: Path) -> Path:
    env = os.environ.get("SUBMISSION_ROOT")
    if env:
        return Path(env).resolve()
    for cand in [start, *start.parents]:
        if (cand / "challenge-pack").is_dir() and (cand / "grading-kit").is_dir():
            return cand
    return start.parents[2]


# --------------------------------------------------------------------------- #
# rubric model
# --------------------------------------------------------------------------- #
@dataclass
class RubricLine:
    key: str
    title: str
    max_points: int
    mode: str  # "auto" | "manual" | "mixed"
    notes: str = ""
    raw: Any = None
    points_awarded: Optional[float] = None  # None => not yet scored (manual)

    def as_entry(self) -> dict:
        return {
            "key": self.key,
            "title": self.title,
            "mode": self.mode,
            "max_points": self.max_points,
            "raw": self.raw,
            "points_awarded": self.points_awarded,
            "notes": self.notes,
        }


# The canonical 100-point rubric (keep in sync with grading-kit/rubric.md).
RUBRIC: list[RubricLine] = [
    RubricLine("nl2sql", "NL-to-SQL", 16, "mixed"),
    RubricLine("ocr", "OCR extraction (tables/handwriting/stitch)", 14, "mixed"),
    RubricLine("langgraph", "LangGraph orchestration (14 nodes + HITL)", 14, "mixed"),
    RubricLine("rag", "RAG + reranker (grounded citations)", 10, "mixed"),
    RubricLine("app", "App: HTMX + auth + Postgres sessions + logging", 10, "mixed"),
    RubricLine("redis_api", "Redis API (cache, rate-limit, idempotency, streams, SSE)", 10, "mixed"),
    RubricLine("ml_underwriting", "ML underwriting (PD model + SHAP reasons)", 8, "mixed"),
    RubricLine("eng_quality", "Engineering quality (tests, structure, types)", 8, "manual"),
    RubricLine("sparql", "SPARQL / ontology reasoning", 4, "mixed"),
    RubricLine("security", "Security & red-team", 4, "mixed"),
    RubricLine("docs", "Docs / ADRs", 2, "manual"),
]
PASS_MARK = 70


def rubric_by_key() -> dict[str, RubricLine]:
    return {r.key: r for r in RUBRIC}


# --------------------------------------------------------------------------- #
# subprocess + threshold helpers
# --------------------------------------------------------------------------- #
def _run(cmd: list[str], cwd: Path, timeout: int = 600) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError as exc:
        return 127, "", f"not found: {exc}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except Exception as exc:  # pragma: no cover - defensive
        return 1, "", f"{type(exc).__name__}: {exc}"


def _extract_json(text: str) -> Optional[dict]:
    """Pull the last JSON object out of mixed stdout (eval CLIs print a metrics dict)."""
    if not text:
        return None
    # try whole text first
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    # else scan for the last balanced {...}
    depth = 0
    start = -1
    candidates: list[str] = []
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                candidates.append(text[start : i + 1])
    for blob in reversed(candidates):
        try:
            obj = json.loads(blob)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


def ladder(value: Optional[float], steps: list[tuple[float, float]], max_points: int) -> float:
    """Map a metric to points via a sorted threshold ladder.

    steps = [(threshold, fraction_of_max), ...] ascending by threshold; the
    highest threshold the value meets wins. Returns 0 if value is None.
    """
    if value is None:
        return 0.0
    awarded = 0.0
    for thr, frac in sorted(steps):
        if value >= thr:
            awarded = frac * max_points
    return round(awarded, 2)


# --------------------------------------------------------------------------- #
# eval-CLI discovery + scoring
# --------------------------------------------------------------------------- #
@dataclass
class EvalSpec:
    rubric_key: str
    script: str                       # relative to challenge-pack/eval/
    args: list[str] = field(default_factory=list)
    metric_keys: list[str] = field(default_factory=list)  # primary metric candidates
    steps: list[tuple[float, float]] = field(default_factory=list)


def default_eval_specs(challenge_pack: Path) -> list[EvalSpec]:
    """The eval CLIs the candidate track ships. We score whichever exist."""
    return [
        EvalSpec(
            "ocr", "ocr_eval.py",
            ["--split", "test", "--pred", "out/ocr", "--gt", os.environ.get(
                "OCR_GT_DIR", str((challenge_pack.parent / "grading-kit/hidden/gt_test"))
            )],
            metric_keys=["teds_struct", "teds", "grits_top", "cell_f1", "f1"],
            steps=[(0.30, 0.25), (0.50, 0.5), (0.70, 0.75), (0.85, 1.0)],
        ),
        EvalSpec(
            "nl2sql", "sql_eval.py",
            ["--split", "test"],
            metric_keys=["execution_accuracy", "exec_acc", "accuracy", "em"],
            steps=[(0.30, 0.25), (0.50, 0.5), (0.70, 0.75), (0.85, 1.0)],
        ),
        EvalSpec(
            "rag", "rag_eval.py",
            ["--split", "test"],
            metric_keys=["ndcg@10", "ndcg", "recall@10", "groundedness", "faithfulness"],
            steps=[(0.30, 0.25), (0.50, 0.5), (0.70, 0.75), (0.85, 1.0)],
        ),
        EvalSpec(
            "ml_underwriting", "underwrite_eval.py",
            ["--split", "test"],
            metric_keys=["auc", "roc_auc", "ks", "pr_auc"],
            steps=[(0.60, 0.25), (0.70, 0.5), (0.78, 0.75), (0.85, 1.0)],
        ),
    ]


def score_eval(spec: EvalSpec, challenge_pack: Path) -> tuple[Optional[float], Any, str]:
    """Run one eval CLI; return (metric_value, raw_payload, note)."""
    script = challenge_pack / "eval" / spec.script
    if not script.is_file():
        return None, None, f"{spec.script} absent — candidate did not ship this eval"
    rc, out, err = _run(
        [sys.executable, str(script), *spec.args], cwd=challenge_pack, timeout=900
    )
    payload = _extract_json(out) or _extract_json(err)
    if payload is None:
        tail = (err or out or "").strip().splitlines()[-3:]
        return None, {"returncode": rc}, f"no JSON metrics emitted (rc={rc}); tail={tail}"
    metric = None
    used_key = None
    for k in spec.metric_keys:
        if k in payload and isinstance(payload[k], (int, float)):
            metric = float(payload[k])
            used_key = k
            break
    note = f"metric={used_key}={metric}" if used_key else "no recognised metric key in output"
    return metric, payload, note


# --------------------------------------------------------------------------- #
# conformance (pytest) scoring
# --------------------------------------------------------------------------- #
def run_pytest_json(test_path: Path, harness_dir: Path, extra: list[str] | None = None) -> dict:
    """Run a pytest file and return a per-test pass/fail map via -p no:cacheprovider + -q.

    We avoid a hard dependency on pytest-json-report: instead we parse the
    machine-readable result-counts and, when available, the verbose node ids.
    """
    extra = extra or []
    # verbose so we can see per-node outcomes; --no-header for stable parsing.
    rc, out, err = _run(
        [sys.executable, "-m", "pytest", str(test_path), "-v", "-rA",
         "-p", "no:cacheprovider", "--no-header", *extra],
        cwd=harness_dir, timeout=600,
    )
    outcomes: dict[str, str] = {}
    for line in (out or "").splitlines():
        # lines like: test_conformance.py::test_at_least_100_tables PASSED [ 10%]
        for status in ("PASSED", "FAILED", "ERROR", "SKIPPED", "XFAIL", "XPASS"):
            token = f" {status}"
            if "::" in line and token in line:
                nodeid = line.split(token, 1)[0].strip()
                outcomes[nodeid] = status
                break
    passed = sum(1 for s in outcomes.values() if s in ("PASSED", "XPASS"))
    failed = sum(1 for s in outcomes.values() if s in ("FAILED", "ERROR"))
    skipped = sum(1 for s in outcomes.values() if s == "SKIPPED")
    return {
        "returncode": rc,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "outcomes": outcomes,
        "ran": bool(outcomes) or rc in (0, 1),
        "stderr_tail": (err or "").strip().splitlines()[-3:],
    }


def _frac(passed: int, total: int) -> float:
    return (passed / total) if total else 0.0


# --------------------------------------------------------------------------- #
# main orchestration
# --------------------------------------------------------------------------- #
def grade(submission_root: Path, out_path: Path) -> dict:
    challenge_pack = submission_root / "challenge-pack"
    grading_kit = find_repo_root(HERE) / "grading-kit"  # harness lives with the grader
    harness_dir = grading_kit / "harness"
    by_key = rubric_by_key()
    diagnostics: dict[str, Any] = {}

    # ---- 1. conformance pytest --------------------------------------------- #
    conf = run_pytest_json(harness_dir / "test_conformance.py", harness_dir)
    diagnostics["conformance"] = {k: conf[k] for k in
                                  ("returncode", "passed", "failed", "skipped", "ran")}

    # Conformance feeds several rubric lines. We bucket node ids by prefix.
    oc = conf["outcomes"]

    def bucket(predicate: Callable[[str], bool]) -> tuple[int, int]:
        items = {n: s for n, s in oc.items() if predicate(n)}
        total = sum(1 for s in items.values() if s != "SKIPPED")
        passed = sum(1 for s in items.values() if s in ("PASSED", "XPASS"))
        return passed, total

    # schema-scale + required schema -> contributes to nl2sql (schema correctness gate)
    sp, st = bucket(lambda n: any(t in n for t in (
        "test_at_least_100_tables", "test_at_least_200_columns",
        "test_required_table_present", "test_required_column_present",
        "test_required_fk_present", "test_ddl_directory_exists")))
    schema_frac = _frac(sp, st)
    diagnostics["schema_conformance"] = {"passed": sp, "total": st, "frac": round(schema_frac, 3)}

    # leakage + manifest + sha -> data-integrity gate (affects ocr line, hard cap)
    lp, lt = bucket(lambda n: any(t in n for t in (
        "manifest", "sha256sums", "leakage", "hidden_gt", "test_pages_exist")))
    data_frac = _frac(lp, lt)
    diagnostics["data_integrity"] = {"passed": lp, "total": lt, "frac": round(data_frac, 3)}

    # ---- 2. eval CLIs ------------------------------------------------------ #
    eval_results: dict[str, dict] = {}
    for spec in default_eval_specs(challenge_pack):
        metric, payload, note = score_eval(spec, challenge_pack)
        line = by_key[spec.rubric_key]
        pts = ladder(metric, spec.steps, line.max_points)
        eval_results[spec.rubric_key] = {
            "metric": metric, "points": pts, "note": note, "payload": payload,
        }
        # auto-set the line from the eval; manual reviewer may bump within max.
        line.raw = {"metric": metric, "source": spec.script}
        line.points_awarded = pts
        line.notes = note
    diagnostics["eval_clis"] = {k: {"metric": v["metric"], "points": v["points"], "note": v["note"]}
                               for k, v in eval_results.items()}

    # NL-to-SQL: blend execution accuracy (eval) with schema conformance gate.
    nl = by_key["nl2sql"]
    exec_pts = eval_results.get("nl2sql", {}).get("points", 0.0)
    # schema correctness is worth up to 4 of the 16; exec accuracy the other 12.
    schema_pts = round(schema_frac * 4, 2)
    exec_scaled = round((exec_pts / 16) * 12, 2) if exec_pts else 0.0
    nl.points_awarded = round(schema_pts + exec_scaled, 2)
    nl.raw = {"exec_metric": eval_results.get("nl2sql", {}).get("metric"),
              "schema_frac": round(schema_frac, 3)}
    nl.notes = f"schema_pts={schema_pts}/4 + exec_pts={exec_scaled}/12"

    # OCR: scale by data-integrity gate (no point scoring OCR if the pack leaked).
    ocr = by_key["ocr"]
    base_ocr = eval_results.get("ocr", {}).get("points", 0.0)
    ocr.points_awarded = round(base_ocr * (data_frac if data_frac else 1.0), 2)
    ocr.notes = (eval_results.get("ocr", {}).get("note", "")
                 + f" | data_integrity_frac={round(data_frac, 3)}")

    # ---- 3. live auth/session (only when APP_BASE_URL set) ----------------- #
    if os.environ.get("APP_BASE_URL"):
        auth = run_pytest_json(harness_dir / "test_auth_session.py", harness_dir)
        diagnostics["auth_session"] = {k: auth[k] for k in
                                       ("returncode", "passed", "failed", "skipped", "ran")}
        ap, at = (auth["passed"], auth["passed"] + auth["failed"])
        auth_frac = _frac(ap, at)
        app_line = by_key["app"]
        # the app line (10) — auth/sessions/logging — earns up to full from live tests.
        app_line.points_awarded = round(auth_frac * app_line.max_points, 2)
        app_line.raw = {"auth_tests_passed": ap, "auth_tests_total": at}
        app_line.notes = f"live auth/session pass rate {ap}/{at}"
    else:
        diagnostics["auth_session"] = {"ran": False, "reason": "APP_BASE_URL unset"}
        by_key["app"].notes = "APP_BASE_URL unset — manual review required"

    # ---- 4. remaining lines: keep auto where we have a signal, else manual - #
    # langgraph / redis_api / sparql / security default to manual unless an eval
    # signal exists; record 0 with a clear note so the human knows to score them.
    for key in ("langgraph", "redis_api", "sparql", "security"):
        line = by_key[key]
        if line.points_awarded is None:
            line.notes = line.notes or "no automated probe — score manually (see rubric.md)"

    # eng_quality and docs are manual by definition.
    for key in ("eng_quality", "docs"):
        by_key[key].notes = "manual review (see rubric.md)"

    # ---- assemble scorecard ------------------------------------------------ #
    entries = [r.as_entry() for r in RUBRIC]
    auto_total = round(sum(
        (r.points_awarded or 0.0) for r in RUBRIC if r.points_awarded is not None
    ), 2)
    auto_max = sum(r.max_points for r in RUBRIC if r.mode in ("auto", "mixed"))
    manual_pending = [r.key for r in RUBRIC if r.points_awarded is None]
    grand_max = sum(r.max_points for r in RUBRIC)

    scorecard = {
        "schema_version": "1.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "submission_root": str(submission_root),
        "rubric_lines": entries,
        "totals": {
            "auto_points_awarded": auto_total,
            "auto_points_possible": auto_max,
            "grand_total_possible": grand_max,
            "pass_mark": PASS_MARK,
            "provisional_pass": auto_total >= PASS_MARK,
            "manual_lines_pending": manual_pending,
        },
        "diagnostics": diagnostics,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(scorecard, indent=2), encoding="utf-8")
    return scorecard


def main(argv: list[str] | None = None) -> int:
    repo_root = find_repo_root(HERE)
    parser = argparse.ArgumentParser(description="AutoLoan-DocIntel auto-grader")
    parser.add_argument(
        "--submission", default=str(repo_root),
        help="path to the submission repo root (default: detected repo root)",
    )
    parser.add_argument(
        "--out", default=str(repo_root / "grading-kit" / "scorecard.json"),
        help="where to write scorecard.json",
    )
    args = parser.parse_args(argv)

    submission_root = Path(args.submission).resolve()
    out_path = Path(args.out).resolve()

    try:
        card = grade(submission_root, out_path)
    except Exception as exc:  # never crash the grader
        out_path.parent.mkdir(parents=True, exist_ok=True)
        err_card = {
            "schema_version": "1.0",
            "error": f"{type(exc).__name__}: {exc}",
            "submission_root": str(submission_root),
            "totals": {"auto_points_awarded": 0, "provisional_pass": False},
        }
        out_path.write_text(json.dumps(err_card, indent=2), encoding="utf-8")
        print(f"GRADER ERROR (wrote degraded scorecard): {exc}", file=sys.stderr)
        return 2

    t = card["totals"]
    print(json.dumps({
        "auto_points_awarded": t["auto_points_awarded"],
        "auto_points_possible": t["auto_points_possible"],
        "provisional_pass": t["provisional_pass"],
        "manual_lines_pending": t["manual_lines_pending"],
        "scorecard": str(out_path),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
