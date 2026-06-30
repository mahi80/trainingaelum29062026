"""Reference (calibration) implementations of the candidate LangGraph nodes.

PRIVATE — grading-kit only. This is the *light* reference used to (a) prove the
challenge is solvable inside the 28h budget and (b) anchor the rubric/SLO ranges.
It deliberately favours simple, defensible heuristics over the best possible
model so the "Target" tier sits comfortably above pass and well below a strong
"Stretch" submission.

Design rules mirrored from src/nodes/nodes.py:
  * Every function takes the GraphState (a dict) and returns a *partial* dict
    that LangGraph merges back into state.
  * Heavy / optional libraries (numpy, lightgbm, sentence-transformers, psycopg,
    shap) are imported lazily *inside* the functions so this module imports
    cleanly with nothing but the stdlib installed.
  * Functions degrade gracefully: when an external dependency (DB, LLM, model
    file, reranker) is unavailable they fall back to a deterministic path and
    set `degraded=True` rather than raising.

Function signatures are drop-in compatible with the six CANDIDATE nodes:
    router_planner, schema_linker, nl2sql_generator,
    vector_retriever, reranker, underwriting_scorer
"""
from __future__ import annotations

import math
import os
import re

# `GraphState` is a TypedDict (i.e. a plain dict at runtime). We annotate with a
# loose alias to avoid importing the candidate package at module-import time.
GraphState = dict


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _tokens(text: str) -> list[str]:
    """Lowercase alphanumeric word tokens."""
    return re.findall(r"[a-z0-9_]+", (text or "").lower())


def _trigrams(text: str) -> set[str]:
    """Character trigram set (pg_trgm-style) for fuzzy schema matching."""
    s = f"  {(text or '').lower().strip()}  "
    return {s[i:i + 3] for i in range(len(s) - 2)}


def _trigram_sim(a: str, b: str) -> float:
    """Jaccard similarity over character trigrams in [0, 1]."""
    ta, tb = _trigrams(a), _trigrams(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


# --------------------------------------------------------------------------- #
# control lane — router_planner
# --------------------------------------------------------------------------- #
# Keyword cues -> route. Order matters: more specific lanes are checked first.
_ROUTE_CUES: dict[str, tuple[str, ...]] = {
    "underwrite": (
        "approve", "approval", "decline", "deny", "underwrite", "underwriting",
        "default", "pd", "probability of default", "risk", "dti", "ltv",
        "credit decision", "should we lend", "adverse action", "reason code",
    ),
    "sparql": (
        "policy", "rule", "supersede", "superseded", "ontology", "eligibility",
        "regulation", "regulatory", "which policy", "governing rule",
    ),
    "vector": (
        "document", "scan", "page", "handwritten", "note", "stipulation letter",
        "what does the", "according to the", "states", "clause", "paragraph",
        "ocr", "image", "uploaded",
    ),
    "sql": (
        "how many", "count", "average", "avg", "sum", "total", "list", "show",
        "top", "per branch", "by month", "between", "applications", "loans",
        "borrowers", "vehicles", "payments", "delinquent", "balance", "amount",
    ),
}


def router_planner(state: GraphState) -> dict:
    """Heuristic intent router.

    Reference strategy (no LLM required, deterministic): score each route by the
    number of keyword cues that appear in the question, with a light priority
    weighting so domain-specific lanes win ties over the generic SQL lane.
    Returns a valid `route` in {sql, sparql, vector, underwrite, hybrid} plus a
    short ordered `plan` of node names the downstream graph will visit.

    A production candidate would replace this with an LLM JSON classifier
    (temperature=0); we keep the heuristic so the harness is reproducible.
    """
    q = (state.get("question") or "").lower()
    toks = set(_tokens(q))

    # priority weights break ties toward specialised lanes
    weight = {"underwrite": 1.30, "sparql": 1.20, "vector": 1.10, "sql": 1.00}
    scores: dict[str, float] = {}
    for route, cues in _ROUTE_CUES.items():
        hits = 0
        for cue in cues:
            if " " in cue:
                if cue in q:
                    hits += 1
            elif cue in toks:
                hits += 1
        scores[route] = hits * weight[route]

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best, best_score = ranked[0]
    second, second_score = ranked[1] if len(ranked) > 1 else ("sql", 0.0)

    # If the top two lanes are both clearly active, run a hybrid plan.
    if best_score > 0 and second_score >= 0.75 * best_score and second_score > 0:
        route = "hybrid"
    elif best_score == 0:
        route = "sql"  # safe default: most questions are tabular
    else:
        route = best

    plan_map = {
        "sql": ["schema_linker", "nl2sql_generator", "sql_validator_repair",
                "sql_executor", "explainer_citation"],
        "vector": ["vector_retriever", "reranker", "explainer_citation"],
        "sparql": ["sparql_ontology_agent", "explainer_citation"],
        "underwrite": ["underwriting_scorer", "policy_compliance_checker",
                       "hitl_gate", "explainer_citation"],
        "hybrid": ["schema_linker", "nl2sql_generator", "sql_validator_repair",
                   "sql_executor", "vector_retriever", "reranker",
                   "aggregator_critic", "explainer_citation"],
    }
    # routing confidence: gap between winner and runner-up, squashed to (0,1]
    gap = best_score - second_score
    confidence = round(1.0 / (1.0 + math.exp(-(0.5 + gap))), 3)

    return {
        "route": route,
        "plan": plan_map[route],
        "confidence": confidence,
        "trace": (state.get("trace") or []) + [
            {"node": "router_planner", "route": route, "scores": scores}
        ],
    }


# --------------------------------------------------------------------------- #
# sql lane — schema_linker
# --------------------------------------------------------------------------- #
# Tiny offline schema catalog (table -> searchable description card). The real
# node embeds the question and does kNN over app.schema_embedding; for the
# reference we ship a trimmed catalog so linking works with no DB or embeddings.
_SCHEMA_CATALOG: dict[str, str] = {
    "loan.loan_application":
        "loan application application_no borrower branch product requested amount "
        "term months status submitted decided vehicle",
    "loan.borrower":
        "borrower party credit band kyc primary branch",
    "loan.party":
        "party person individual business first last name dob ssn",
    "loan.vehicle":
        "vehicle vin make model year fuel type condition mileage msrp price collateral",
    "loan.underwriting_decision":
        "underwriting decision approve decline dti ratio ltv pd score risk rating apr",
    "loan.loan":
        "loan principal apr term status booked",
    "loan.payment":
        "payment due date paid amount late delinquent installment",
    "loan.delinquency":
        "delinquency bucket days past due dpd",
    "loan.credit_pull":
        "credit pull bureau fico vantage score",
    "loan.branch":
        "branch office region opened active location",
    "loan.policy_rule":
        "policy rule code supersedes region eligibility",
    "loan.employment_record":
        "employment employer income monthly job",
    "doc.document":
        "document scan source path page count class type ingested",
    "doc.document_chunk":
        "document chunk content embedding prose table cell handwriting page",
}


def schema_linker(state: GraphState) -> dict:
    """Link the question to a minimal sub-schema.

    Reference strategy: blend keyword overlap with character-trigram similarity
    (a stand-in for pg_trgm) against the offline catalog cards, then keep the
    top-k tables. Also surfaces a coarse `linked_columns` list scraped from the
    matched cards so nl2sql has column hints even without a DB.
    """
    q = state.get("question") or ""
    q_toks = set(_tokens(q))

    scored: list[tuple[float, str]] = []
    for table, card in _SCHEMA_CATALOG.items():
        card_toks = set(_tokens(card))
        overlap = len(q_toks & card_toks) / (len(q_toks) or 1)
        # also reward matches on the bare table name (e.g. "vehicles" -> vehicle)
        name = table.split(".")[-1]
        trig = max(_trigram_sim(q, name), _trigram_sim(q, table))
        score = 0.7 * overlap + 0.3 * trig
        if score > 0:
            scored.append((score, table))

    scored.sort(reverse=True)
    top_k = 5
    linked_tables = [t for _, t in scored[:top_k]]
    if not linked_tables:
        # never leave the lane empty; the central fact table is a safe anchor
        linked_tables = ["loan.loan_application"]

    # collect candidate columns mentioned across the matched cards
    linked_columns: list[str] = []
    for t in linked_tables:
        for word in _SCHEMA_CATALOG[t].split():
            if word in q_toks and word not in linked_columns:
                linked_columns.append(word)

    # naive join hints: every linked table joins back to loan_application
    join_hints = [
        f"{t} -> loan.loan_application" for t in linked_tables
        if t != "loan.loan_application"
    ]

    return {
        "linked_tables": linked_tables,
        "linked_columns": linked_columns,
        "join_hints": join_hints,
        "trace": (state.get("trace") or []) + [
            {"node": "schema_linker", "linked_tables": linked_tables}
        ],
    }


# --------------------------------------------------------------------------- #
# sql lane — nl2sql_generator
# --------------------------------------------------------------------------- #
_NL2SQL_SYSTEM = (
    "You are a senior PostgreSQL engineer. Translate the user's question into a "
    "single read-only SELECT statement. Rules: SELECT only (no INSERT/UPDATE/"
    "DELETE/DDL, no semicolons beyond one statement); use ONLY the tables and "
    "columns provided in the linked sub-schema; prefer explicit JOINs over the "
    "given join hints; add LIMIT 100 unless the question asks for an aggregate. "
    'Respond as JSON: {"sql": "<statement>", "rationale": "<one sentence>"}.'
)


def _build_nl2sql_prompt(state: GraphState) -> str:
    tables = state.get("linked_tables") or ["loan.loan_application"]
    cols = state.get("linked_columns") or []
    hints = state.get("join_hints") or []
    sub_schema = "\n".join(
        f"  - {t}: {_SCHEMA_CATALOG.get(t, '(columns omitted)')}" for t in tables
    )
    return (
        f"Question:\n  {state.get('question', '')}\n\n"
        f"Linked sub-schema:\n{sub_schema}\n\n"
        f"Column hints: {', '.join(cols) or '(none)'}\n"
        f"Join hints: {', '.join(hints) or '(none)'}\n\n"
        "Return the JSON object now."
    )


def nl2sql_generator(state: GraphState) -> dict:
    """Generate a Postgres SELECT via the project LLM in JSON mode.

    Builds the prompt from the linked sub-schema and calls
    `src.llm.get_llm().complete(as_json=True, temperature=0)`. If the LLM is
    unreachable (no Ollama in the grading sandbox) it falls back to a safe
    templated count over the primary linked table and flags `degraded`.
    """
    prompt = _build_nl2sql_prompt(state)
    sql_draft = ""
    degraded = False
    try:
        from src.llm import get_llm  # lazy: candidate package + network

        raw = get_llm().complete(
            prompt, system=_NL2SQL_SYSTEM, as_json=True, temperature=0.0
        )
        import json

        obj = json.loads(raw)
        sql_draft = (obj.get("sql") or "").strip()
    except Exception:  # noqa: BLE001 — any failure -> deterministic fallback
        degraded = True

    if not sql_draft:
        primary = (state.get("linked_tables") or ["loan.loan_application"])[0]
        sql_draft = f"SELECT count(*) AS n FROM {primary}"
        degraded = True

    return {
        "sql_draft": sql_draft,
        "degraded": degraded or state.get("degraded", False),
        "trace": (state.get("trace") or []) + [
            {"node": "nl2sql_generator", "degraded": degraded}
        ],
    }


# --------------------------------------------------------------------------- #
# retrieval lane — vector_retriever
# --------------------------------------------------------------------------- #
def vector_retriever(state: GraphState) -> dict:
    """Dense kNN over doc.document_chunk using the pgvector `<=>` operator.

    Reference strategy: embed the question with the project LLM, then run a
    cosine-distance ORDER BY over the HNSW index and return the top-N as
    `candidates`. (The full candidate node fuses this with Postgres FTS via RRF;
    the reference keeps the dense half, which is enough to anchor recall.)

    Falls back to an empty candidate list + `degraded` when DATABASE_URL or the
    embedder is unavailable.
    """
    top_n = int(os.environ.get("RETRIEVE_TOP_N", "40"))
    question = state.get("question") or ""
    dsn = os.environ.get("DATABASE_URL")

    if not dsn:
        return {
            "candidates": [],
            "degraded": True,
            "trace": (state.get("trace") or []) + [
                {"node": "vector_retriever", "reason": "no DATABASE_URL"}
            ],
        }

    try:
        from src.llm import get_llm  # lazy

        qvec = get_llm().embed([question])[0]
        # pgvector wants the literal '[a,b,c]' form for a vector parameter
        vec_literal = "[" + ",".join(f"{x:.6f}" for x in qvec) + "]"

        import psycopg  # lazy

        sql = (
            "SELECT chunk_id, page_id, chunk_type, content, "
            "       1 - (embedding <=> %s::vector) AS cosine_sim "
            "FROM doc.document_chunk "
            "WHERE embedding IS NOT NULL "
            "ORDER BY embedding <=> %s::vector "
            "LIMIT %s"
        )
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("SET statement_timeout = '5s'")
            cur.execute(sql, (vec_literal, vec_literal, top_n))
            cols = [d.name for d in cur.description]
            candidates = [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        return {
            "candidates": [],
            "degraded": True,
            "trace": (state.get("trace") or []) + [
                {"node": "vector_retriever", "error": str(exc)[:120]}
            ],
        }

    return {
        "candidates": candidates,
        "trace": (state.get("trace") or []) + [
            {"node": "vector_retriever", "n_candidates": len(candidates)}
        ],
    }


# --------------------------------------------------------------------------- #
# retrieval lane — reranker
# --------------------------------------------------------------------------- #
def reranker(state: GraphState) -> dict:
    """Cross-encoder rerank of candidates -> top-k contexts.

    Uses a sentence-transformers CrossEncoder (bge-reranker-v2-m3) when the
    library + model are present; otherwise falls back to identity ordering
    (preserving the retriever's cosine ranking) so the lane still produces
    contexts. Records per-context `rerank_score` and a `retrieval_scores`
    summary so the harness can measure nDCG lift.
    """
    candidates = state.get("candidates") or []
    top_k = int(os.environ.get("RERANK_TOP_K", "6"))
    question = state.get("question") or ""

    if not candidates:
        return {"contexts": [], "retrieval_scores": {"reranked": False}}

    used_cross_encoder = False
    try:
        from sentence_transformers import CrossEncoder  # lazy, optional

        model_name = os.environ.get("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
        ce = CrossEncoder(model_name)
        pairs = [(question, c.get("content", "")) for c in candidates]
        scores = ce.predict(pairs)
        for c, s in zip(candidates, scores):
            c["rerank_score"] = float(s)
        ranked = sorted(candidates, key=lambda c: c["rerank_score"], reverse=True)
        used_cross_encoder = True
    except Exception:  # noqa: BLE001 — identity fallback on cosine_sim
        for c in candidates:
            c["rerank_score"] = float(c.get("cosine_sim", 0.0))
        ranked = sorted(candidates, key=lambda c: c["rerank_score"], reverse=True)

    contexts = ranked[:top_k]
    # normalise a `page` field the explainer expects (page_id stands in for page)
    for c in contexts:
        c.setdefault("page", c.get("page_id"))

    return {
        "contexts": contexts,
        "retrieval_scores": {
            "reranked": used_cross_encoder,
            "top_score": contexts[0]["rerank_score"] if contexts else 0.0,
            "n_in": len(candidates),
            "n_out": len(contexts),
        },
        "trace": (state.get("trace") or []) + [
            {"node": "reranker", "cross_encoder": used_cross_encoder}
        ],
    }


# --------------------------------------------------------------------------- #
# ml lane — underwriting_scorer
# --------------------------------------------------------------------------- #
# Logistic-fallback coefficients (hand-tuned, monotonic in the obvious
# direction) used when no trained LightGBM artifact is present. Features are the
# normalised columns produced by train_underwriting._featurize.
_LOGIT_INTERCEPT = -1.10
_LOGIT_COEF = {
    "dti_ratio": 3.40,        # higher DTI -> higher PD
    "ltv_ratio": 2.10,        # higher LTV -> higher PD
    "fico_norm": -3.60,       # higher FICO -> lower PD
    "term_norm": 0.80,        # longer term -> slightly higher PD
    "amount_norm": 0.60,      # larger ask -> slightly higher PD
    "used_vehicle": 0.45,     # used collateral -> higher PD
    "thin_file": 0.70,        # few tradelines -> higher PD
}

# Human-readable adverse-action reasons keyed by feature (Reg B / ECOA style).
_ADVERSE_ACTION = {
    "dti_ratio": "Debt-to-income ratio too high",
    "ltv_ratio": "Loan-to-value ratio too high for collateral",
    "fico_norm": "Credit score below program threshold",
    "term_norm": "Requested term exceeds program guidelines",
    "amount_norm": "Requested amount high relative to profile",
    "used_vehicle": "Collateral condition increases risk",
    "thin_file": "Insufficient credit history",
}

_APPROVE_MAX = 0.15   # PD below this -> approve
_DENY_MIN = 0.45      # PD above this -> deny; in-between -> refer


def _featurize(features: dict) -> dict:
    """Map a raw applicant_features dict onto the model's normalised feature
    space. Mirrors train_underwriting._featurize so train/serve agree.
    """
    def _num(key: str, default: float = 0.0) -> float:
        try:
            return float(features.get(key, default))
        except (TypeError, ValueError):
            return default

    fico = _num("fico_score", 680.0)
    return {
        "dti_ratio": min(max(_num("dti_ratio", 0.36), 0.0), 1.5),
        "ltv_ratio": min(max(_num("ltv_ratio", 0.90), 0.0), 2.0),
        "fico_norm": (fico - 300.0) / 550.0,            # 300..850 -> 0..1
        "term_norm": min(_num("requested_term_months", 60.0) / 84.0, 1.5),
        "amount_norm": min(_num("requested_amount", 25000.0) / 100000.0, 2.0),
        "used_vehicle": 1.0 if str(features.get("condition", "")).lower()
        in {"used", "cpo"} else 0.0,
        "thin_file": 1.0 if _num("tradeline_count", 5.0) < 3 else 0.0,
    }


def _logistic_pd(fv: dict) -> float:
    z = _LOGIT_INTERCEPT + sum(_LOGIT_COEF[k] * fv.get(k, 0.0) for k in _LOGIT_COEF)
    return 1.0 / (1.0 + math.exp(-z))


def underwriting_scorer(state: GraphState) -> dict:
    """Score probability-of-default and bucket into approve / refer / deny.

    Loads a trained LightGBM model from MODEL_PATH (default
    grading-kit/reference-solution/artifacts/pd_lgbm.txt) when present and the
    library is installed; otherwise uses the transparent logistic fallback. In
    both cases it attaches ranked adverse-action `reason_codes` — SHAP-derived
    when a model + shap are available, contribution-derived for the logistic
    path — so the explainability requirement is met without the trained artifact.
    """
    raw_features = state.get("applicant_features") or {}
    fv = _featurize(raw_features)

    model_path = os.environ.get(
        "MODEL_PATH",
        os.path.join(os.path.dirname(__file__), "artifacts", "pd_lgbm.txt"),
    )
    feature_order = list(_LOGIT_COEF.keys())
    pd_score: float
    reason_codes: list[dict]
    model_kind = "logistic_fallback"

    if os.path.exists(model_path):
        try:
            import lightgbm as lgb  # lazy, optional

            booster = lgb.Booster(model_file=model_path)
            # respect the feature order baked into the model if available
            names = booster.feature_name() or feature_order
            row = [[fv.get(n, 0.0) for n in names]]
            pd_score = float(booster.predict(row)[0])
            model_kind = "lightgbm"
            reason_codes = _shap_reasons(booster, row, names, fv)
        except Exception:  # noqa: BLE001 — fall back to logistic
            pd_score = _logistic_pd(fv)
            reason_codes = _logit_reasons(fv)
    else:
        pd_score = _logistic_pd(fv)
        reason_codes = _logit_reasons(fv)

    pd_score = float(min(max(pd_score, 0.0), 1.0))
    if pd_score <= _APPROVE_MAX:
        decision = "approve"
    elif pd_score >= _DENY_MIN:
        decision = "deny"
    else:
        decision = "refer"

    # confidence = distance from the nearest decision boundary, scaled
    nearest = min(abs(pd_score - _APPROVE_MAX), abs(pd_score - _DENY_MIN))
    pd_confidence = round(min(1.0, 0.5 + nearest * 2.0), 3)

    return {
        "decision": decision,
        "pd_score": round(pd_score, 4),
        "pd_confidence": pd_confidence,
        "reason_codes": reason_codes if decision != "approve" else [],
        "trace": (state.get("trace") or []) + [
            {"node": "underwriting_scorer", "model": model_kind,
             "pd_score": round(pd_score, 4), "decision": decision}
        ],
    }


def _logit_reasons(fv: dict, top: int = 4) -> list[dict]:
    """Rank features by their *positive* contribution to PD (push toward deny)."""
    contribs = []
    for k, coef in _LOGIT_COEF.items():
        contribs.append((k, coef * fv.get(k, 0.0)))
    contribs.sort(key=lambda kv: kv[1], reverse=True)
    out = []
    for k, c in contribs[:top]:
        if c <= 0:
            continue
        out.append({
            "code": k,
            "reason": _ADVERSE_ACTION.get(k, k),
            "contribution": round(float(c), 4),
        })
    return out


def _shap_reasons(booster, row, names, fv, top: int = 4) -> list[dict]:
    """SHAP-based adverse-action reasons; falls back to gain if shap missing."""
    try:
        import shap  # lazy, optional

        explainer = shap.TreeExplainer(booster)
        vals = explainer.shap_values(row)
        # binary objective -> shap_values may be a list; take the positive class
        sv = vals[1] if isinstance(vals, list) else vals
        pairs = sorted(zip(names, sv[0]), key=lambda kv: kv[1], reverse=True)
        out = []
        for name, val in pairs[:top]:
            if val <= 0:
                continue
            out.append({
                "code": name,
                "reason": _ADVERSE_ACTION.get(name, name),
                "contribution": round(float(val), 4),
            })
        return out or _logit_reasons(fv, top)
    except Exception:  # noqa: BLE001
        return _logit_reasons(fv, top)
