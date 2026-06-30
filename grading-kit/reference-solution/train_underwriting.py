"""Train the reference probability-of-default (PD) model for underwriting.

PRIVATE — grading-kit only. This is the *light* reference trainer used to anchor
the underwriting metric ranges (AUC / KS) in the rubric. It is intentionally
small and need not run inside this scaffold; it is documented so an evaluator can
regenerate `artifacts/pd_lgbm.txt` + `artifacts/adverse_action_map.json` from the
seeded warehouse.

Pipeline
--------
1. Pull a training frame from the loan warehouse (loan.loan_application joined to
   borrower / vehicle / credit_pull / underwriting_decision / delinquency). The
   label is "defaulted" — derived from delinquency 90+ DPD or a charged-off loan
   status; if no such history exists the seeded `underwriting_decision.pd_score`
   is thresholded to produce a weak label so the trainer still demonstrates.
2. Featurize with the SAME `_featurize` used at serve time in nodes_ref.py
   (single source of truth -> no train/serve skew).
3. Train LightGBM (binary logloss). If lightgbm is unavailable, fall back to a
   scikit-learn LogisticRegression and persist its coefficients instead.
4. Compute a global SHAP -> adverse-action mapping (feature -> human reason) and
   save it next to the model.

Heavy/optional imports (psycopg, pandas, numpy, lightgbm, sklearn, shap) are all
lazy so this module imports with only the stdlib present.

Usage
-----
    python grading-kit/reference-solution/train_underwriting.py \
        --dsn "$DATABASE_URL" --out grading-kit/reference-solution/artifacts
"""
from __future__ import annotations

import argparse
import json
import os

# Reuse the exact serve-time featurizer + adverse-action labels so the trained
# model and nodes_ref.underwriting_scorer never disagree on the feature space.
from nodes_ref import _ADVERSE_ACTION, _LOGIT_COEF, _featurize  # noqa: E402

FEATURE_ORDER = list(_LOGIT_COEF.keys())

# --------------------------------------------------------------------------- #
# data loading
# --------------------------------------------------------------------------- #
_TRAIN_SQL = """
SELECT
    a.application_id,
    a.requested_amount,
    a.requested_term_months,
    v.condition                         AS condition,
    cp.fico_score                       AS fico_score,
    ud.dti_ratio                        AS dti_ratio,
    ud.ltv_ratio                        AS ltv_ratio,
    ud.pd_score                         AS seed_pd_score,
    COALESCE(tl.tradeline_count, 0)     AS tradeline_count,
    COALESCE(dq.max_dpd, 0)             AS max_dpd,
    COALESCE(l.status, 'none')          AS loan_status
FROM loan.loan_application a
JOIN loan.borrower b              ON b.borrower_id = a.borrower_id
LEFT JOIN loan.vehicle v         ON v.vehicle_id = a.vehicle_id
LEFT JOIN LATERAL (
    SELECT fico_score FROM loan.credit_pull cp
    WHERE cp.borrower_id = a.borrower_id
    ORDER BY cp.pulled_at DESC LIMIT 1
) cp ON true
LEFT JOIN loan.underwriting_decision ud ON ud.application_id = a.application_id
LEFT JOIN loan.loan l            ON l.application_id = a.application_id
LEFT JOIN LATERAL (
    SELECT count(*) AS tradeline_count
    FROM loan.credit_pull cp2
    JOIN loan.tradeline t ON t.credit_pull_id = cp2.credit_pull_id
    WHERE cp2.borrower_id = a.borrower_id
) tl ON true
LEFT JOIN LATERAL (
    SELECT max(d.days_past_due) AS max_dpd
    FROM loan.loan l2
    JOIN loan.delinquency d ON d.loan_id = l2.loan_id
    WHERE l2.application_id = a.application_id
) dq ON true
"""


def load_frame(dsn: str):
    """Return a list of row dicts from the warehouse (pandas optional)."""
    import psycopg  # lazy

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(_TRAIN_SQL)
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return rows


def make_label(row: dict) -> int:
    """Default label: hard signal if available, else weak label from seed PD."""
    if (row.get("max_dpd") or 0) >= 90:
        return 1
    if str(row.get("loan_status", "")).lower() in {"charged_off", "default"}:
        return 1
    seed = row.get("seed_pd_score")
    if seed is not None:
        try:
            return 1 if float(seed) >= 0.5 else 0
        except (TypeError, ValueError):
            return 0
    return 0


def build_xy(rows: list[dict]):
    """Return (X as list[list[float]], y as list[int]) in FEATURE_ORDER."""
    X, y = [], []
    for r in rows:
        fv = _featurize({
            "dti_ratio": r.get("dti_ratio"),
            "ltv_ratio": r.get("ltv_ratio"),
            "fico_score": r.get("fico_score"),
            "requested_term_months": r.get("requested_term_months"),
            "requested_amount": r.get("requested_amount"),
            "condition": r.get("condition"),
            "tradeline_count": r.get("tradeline_count"),
        })
        X.append([fv[k] for k in FEATURE_ORDER])
        y.append(make_label(r))
    return X, y


# --------------------------------------------------------------------------- #
# training
# --------------------------------------------------------------------------- #
def train(X, y, out_dir: str) -> dict:
    """Train LightGBM (preferred) or sklearn LogisticRegression (fallback).

    Returns a small metrics dict and writes the model artifact under out_dir.
    """
    os.makedirs(out_dir, exist_ok=True)
    metrics: dict = {"n_samples": len(y), "n_pos": int(sum(y))}

    try:
        import lightgbm as lgb  # lazy, optional

        train_set = lgb.Dataset(X, label=y, feature_name=FEATURE_ORDER)
        params = {
            "objective": "binary",
            "metric": ["auc", "binary_logloss"],
            "learning_rate": 0.05,
            "num_leaves": 15,
            "min_data_in_leaf": 20,
            "feature_fraction": 0.9,
            "verbose": -1,
        }
        booster = lgb.train(params, train_set, num_boost_round=200)
        model_path = os.path.join(out_dir, "pd_lgbm.txt")
        booster.save_model(model_path)
        metrics["model"] = "lightgbm"
        metrics["model_path"] = model_path
        _save_adverse_map(out_dir, booster=booster, X=X)
    except Exception as exc:  # noqa: BLE001 — sklearn fallback
        metrics.update(_train_sklearn(X, y, out_dir))
        metrics["lgbm_error"] = str(exc)[:120]
    return metrics


def _train_sklearn(X, y, out_dir: str) -> dict:
    """Logistic-regression fallback; persists coefficients as JSON."""
    from sklearn.linear_model import LogisticRegression  # lazy

    clf = LogisticRegression(max_iter=1000)
    clf.fit(X, y)
    coef = {name: float(c) for name, c in zip(FEATURE_ORDER, clf.coef_[0])}
    payload = {"intercept": float(clf.intercept_[0]), "coef": coef,
               "feature_order": FEATURE_ORDER}
    path = os.path.join(out_dir, "pd_logistic.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    _save_adverse_map(out_dir, coef=coef)
    return {"model": "sklearn_logistic", "model_path": path}


# --------------------------------------------------------------------------- #
# explainability — SHAP -> adverse-action mapping
# --------------------------------------------------------------------------- #
def _save_adverse_map(out_dir: str, booster=None, X=None, coef=None) -> None:
    """Persist {feature -> human reason, global_importance} for Reg B reasons."""
    importance: dict[str, float] = {}
    if booster is not None and X is not None:
        try:
            import numpy as np  # lazy
            import shap  # lazy

            explainer = shap.TreeExplainer(booster)
            sample = X[:500]
            sv = explainer.shap_values(sample)
            sv = sv[1] if isinstance(sv, list) else sv
            mean_abs = np.abs(sv).mean(axis=0)
            importance = {n: float(v) for n, v in zip(FEATURE_ORDER, mean_abs)}
        except Exception:  # noqa: BLE001 — fall back to gain importance
            try:
                gains = booster.feature_importance(importance_type="gain")
                importance = {n: float(g)
                              for n, g in zip(booster.feature_name(), gains)}
            except Exception:  # noqa: BLE001
                importance = {}
    elif coef is not None:
        importance = {n: abs(c) for n, c in coef.items()}

    mapping = {
        feat: {
            "reason": _ADVERSE_ACTION.get(feat, feat),
            "global_importance": round(importance.get(feat, 0.0), 6),
        }
        for feat in FEATURE_ORDER
    }
    path = os.path.join(out_dir, "adverse_action_map.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(mapping, fh, indent=2)


# --------------------------------------------------------------------------- #
# entrypoint
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train reference PD model.")
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"),
                        help="Postgres DSN (defaults to $DATABASE_URL).")
    parser.add_argument(
        "--out",
        default=os.path.join(os.path.dirname(__file__), "artifacts"),
        help="Output directory for model + adverse-action map.",
    )
    args = parser.parse_args(argv)

    if not args.dsn:
        raise SystemExit("DATABASE_URL / --dsn required to load the training frame.")

    rows = load_frame(args.dsn)
    if not rows:
        raise SystemExit("No rows returned; is the warehouse seeded?")
    X, y = build_xy(rows)
    metrics = train(X, y, args.out)
    print(json.dumps(metrics, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
