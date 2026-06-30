#!/usr/bin/env python3
"""Weekly consultant-status checker for the AutoLoan-DocIntel take-home.

Scans every `solution/*` branch and reports, per consultant:
  - commits in Week 1 (days 0-7) vs Week 2 (days 8-14) of their window,
  - total commits ahead of base, last activity, files-changed count,
  - whether the branch touched `grading-kit/` (it MUST NOT — flagged),
  - open PR number/state/URL and CI rollup (if `gh` is available).

The week window per consultant defaults to their branch's FIRST commit date,
or use --start to pin a common cohort start. Output is a Markdown table on
stdout plus an optional CSV you can paste into consultant-weekly-tracker.xlsx.

Run (fetch first so remote branches are visible):
  git fetch --all --prune
  python grading-kit/harness/consultant_status.py --csv status.csv
  python grading-kit/harness/consultant_status.py --start 2026-07-01
"""
from __future__ import annotations
import argparse
import csv as csvmod
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

try:  # emit UTF-8 even on legacy Windows code pages
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def git(*args, cwd="."):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True).stdout.strip()


def have_gh():
    try:
        subprocess.run(["gh", "--version"], capture_output=True, check=True)
        return True
    except Exception:
        return False


def solution_branches(base_remote):
    """Return {consultant: ref} for local and remote solution/* branches."""
    refs = {}
    out = git("for-each-ref", "--format=%(refname)",
              "refs/heads/solution", f"refs/remotes/{base_remote}/solution")
    for ref in [r for r in out.splitlines() if r.strip()]:
        # refs/heads/solution/alice  OR  refs/remotes/origin/solution/alice
        name = ref.split("/solution/", 1)[1]
        # prefer remote ref if both exist (latest pushed state)
        if name not in refs or ref.startswith("refs/remotes/"):
            refs[name] = ref
    return refs


def count_range(base, ref, since=None, until=None):
    args = ["rev-list", "--count", f"{base}..{ref}"]
    if since:
        args.append(f"--since={since}")
    if until:
        args.append(f"--until={until}")
    out = git(*args)
    return int(out) if out.isdigit() else 0


def first_commit_iso(base, ref):
    out = git("log", "--reverse", "--format=%cI", f"{base}..{ref}")
    return out.splitlines()[0] if out else ""


def gh_pr(ref_short):
    """Return (number, state, url, ci) for the branch's PR, or blanks."""
    try:
        out = subprocess.run(
            ["gh", "pr", "list", "--head", ref_short, "--state", "all",
             "--json", "number,state,url,statusCheckRollup"],
            capture_output=True, text=True, check=True).stdout
        arr = json.loads(out or "[]")
        if not arr:
            return ("", "", "", "")
        pr = arr[0]
        roll = pr.get("statusCheckRollup") or []
        concl = {c.get("conclusion") or c.get("state") for c in roll}
        ci = ("fail" if {"FAILURE", "ERROR", "CANCELLED"} & concl
              else "pass" if concl and concl <= {"SUCCESS", "NEUTRAL", "SKIPPED", "COMPLETED"}
              else "pending" if roll else "")
        return (str(pr["number"]), pr["state"].lower(), pr["url"], ci)
    except Exception:
        return ("", "", "", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--base", default="main")
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--start", help="cohort start YYYY-MM-DD (default: each branch's first commit)")
    ap.add_argument("--csv", help="write a CSV here for the tracker workbook")
    args = ap.parse_args()

    global git
    _git = git
    git = lambda *a: _git(*a, cwd=args.repo)  # noqa: E731

    branches = solution_branches(args.remote)
    gh = have_gh()
    rows = []
    for name in sorted(branches):
        ref = branches[name]
        ref_short = ref.split("refs/heads/")[-1].split(f"refs/remotes/{args.remote}/")[-1]
        start_iso = args.start or first_commit_iso(args.base, ref)
        if start_iso:
            try:
                start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
            except ValueError:
                start = datetime.now(timezone.utc)
        else:
            start = datetime.now(timezone.utc)
        mid = (start + timedelta(days=7)).isoformat()
        end = (start + timedelta(days=14)).isoformat()
        wk1 = count_range(args.base, ref, since=start.isoformat(), until=mid)
        wk2 = count_range(args.base, ref, since=mid, until=end)
        total = count_range(args.base, ref)
        last = git("log", "-1", "--format=%cI %an", ref)
        changed = git("diff", "--name-only", f"{args.base}...{ref}").splitlines()
        gk = sum(1 for f in changed if f.startswith("grading-kit/"))
        pr_num, pr_state, pr_url, ci = gh_pr(ref_short) if gh else ("", "", "", "")
        rows.append({
            "consultant": name, "branch": ref_short,
            "start": start.date().isoformat(),
            "wk1_commits": wk1, "wk2_commits": wk2, "total_ahead": total,
            "last_activity": last, "files_changed": len(changed),
            "grading_kit_touched": gk, "pr": pr_num, "pr_state": pr_state,
            "ci": ci, "pr_url": pr_url,
        })

    # Markdown table
    print(f"# Consultant status ({datetime.now(timezone.utc).date().isoformat()})  "
          f"base={args.base}  branches={len(rows)}\n")
    if not rows:
        print("_No `solution/*` branches found. Run `git fetch --all --prune` first._")
    else:
        print("| Consultant | Wk1 | Wk2 | Ahead | Last activity | PR | CI | grading-kit? |")
        print("|---|--:|--:|--:|---|---|---|---|")
        for r in rows:
            flag = f"FLAG ({r['grading_kit_touched']})" if r["grading_kit_touched"] else "ok"
            pr = f"#{r['pr']} ({r['pr_state']})" if r["pr"] else "-"
            print(f"| {r['consultant']} | {r['wk1_commits']} | {r['wk2_commits']} | "
                  f"{r['total_ahead']} | {r['last_activity']} | {pr} | {r['ci'] or '-'} | {flag} |")
        print("\n_Paste the commit columns into the 'Weekly Tracker' tab. "
              "Any non-zero grading-kit? is a red flag (candidates must not touch it)._")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csvmod.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else
                                  ["consultant", "branch", "wk1_commits", "wk2_commits"])
            w.writeheader()
            w.writerows(rows)
        print(f"\nwrote {args.csv}")


if __name__ == "__main__":
    main()
