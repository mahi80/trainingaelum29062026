"""The 14 LangGraph nodes.

CANDIDATE nodes raise NotImplementedError with a TODO — implement these.
PROVIDED-STUB nodes ship with minimal working logic so the graph runs and the
vertical slice can be built incrementally.

Every node takes the GraphState and returns a partial-state dict (LangGraph
merges it). Keep nodes pure and side-effect-light; do I/O via injected clients.
"""
from __future__ import annotations
import os
from ..graph.state import GraphState


# --------------------------------------------------------------------------- #
# control lane
# --------------------------------------------------------------------------- #
def router_planner(state: GraphState) -> dict:
    """CANDIDATE: classify intent -> route in {sql,sparql,vector,underwrite,hybrid}
    and produce a short plan. Use the LLM in JSON mode (temperature=0)."""
    raise NotImplementedError("TODO(candidate): route the question and plan steps")


def aggregator_critic(state: GraphState) -> dict:
    """CANDIDATE: merge multi-route partials, self-check faithfulness, and signal
    a retry (critic_retries) when the answer is not grounded. Cap retries at 2."""
    raise NotImplementedError("TODO(candidate): merge + faithfulness self-check")


def memory(state: GraphState) -> dict:
    """PROVIDED-STUB: read short-term memory. Replace with Redis + pgvector recall."""
    return {"memory_summary": state.get("memory_summary", ""),
            "entities": state.get("entities", {})}


def hitl_gate(state: GraphState) -> dict:
    """CANDIDATE: raise a LangGraph interrupt() when decision == 'deny',
    pd_confidence < 0.6, loan amount is large, or compliance_flags exist."""
    raise NotImplementedError("TODO(candidate): human-in-the-loop interrupt/resume")


# --------------------------------------------------------------------------- #
# sql lane
# --------------------------------------------------------------------------- #
def schema_linker(state: GraphState) -> dict:
    """CANDIDATE: embed the question, kNN over app.schema_embedding (pgvector),
    expand via OpenMetadata lineage/FKs into a minimal sub-schema."""
    raise NotImplementedError("TODO(candidate): schema linking via OpenMetadata + pgvector")


def nl2sql_generator(state: GraphState) -> dict:
    """CANDIDATE: generate Postgres SELECT from question + linked sub-schema using
    Qwen2.5-coder (JSON mode). SELECT-only; only linked tables/columns."""
    raise NotImplementedError("TODO(candidate): NL -> SQL generation")


def sql_validator_repair(state: GraphState) -> dict:
    """CANDIDATE: parse with sqlglot (postgres), allowlist tables/cols, block
    DDL/DML/multi-statement, EXPLAIN dry-run, repair loop (<=3) on error."""
    raise NotImplementedError("TODO(candidate): validate + repair SQL")


def sql_executor(state: GraphState) -> dict:
    """PROVIDED-STUB: execute validated SELECT under a read-only role with a
    statement timeout and a hard row cap."""
    sql = state.get("sql_valid")
    if not sql:
        return {"rows": [], "row_count": 0, "truncated": False}
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return {"rows": [], "row_count": 0, "truncated": False,
                "sql_error": "DATABASE_URL not set"}
    import psycopg
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '5s'")
            cur.execute("BEGIN READ ONLY")
            cur.execute(sql)
            cols = [d.name for d in cur.description] if cur.description else []
            rows = [dict(zip(cols, r)) for r in cur.fetchmany(1000)]
    return {"rows": rows, "row_count": len(rows), "truncated": len(rows) >= 1000}


# --------------------------------------------------------------------------- #
# sparql lane
# --------------------------------------------------------------------------- #
def sparql_ontology_agent(state: GraphState) -> dict:
    """CANDIDATE (Stretch): NL -> SPARQL over the auto-loan ontology (Oxigraph).
    SELECT/ASK only; validate prefixes; repair loop <=2."""
    raise NotImplementedError("TODO(candidate): NL -> SPARQL over ontology")


# --------------------------------------------------------------------------- #
# retrieval lane
# --------------------------------------------------------------------------- #
def vector_retriever(state: GraphState) -> dict:
    """CANDIDATE: hybrid retrieval (Postgres FTS + pgvector kNN), fused with RRF,
    over doc.document_chunk. Return top-40 candidates."""
    raise NotImplementedError("TODO(candidate): hybrid retrieval (BM25 + dense, RRF)")


def reranker(state: GraphState) -> dict:
    """CANDIDATE: cross-encoder rerank (bge-reranker-v2-m3) of candidates -> top-6
    contexts. Record scores so the nDCG lift over no-rerank is measurable."""
    raise NotImplementedError("TODO(candidate): cross-encoder rerank")


# --------------------------------------------------------------------------- #
# ml lane
# --------------------------------------------------------------------------- #
def underwriting_scorer(state: GraphState) -> dict:
    """CANDIDATE: load the trained LightGBM PD model, score applicant_features,
    bucket into approve/refer/deny, attach SHAP-derived adverse-action reasons."""
    raise NotImplementedError("TODO(candidate): LightGBM PD + SHAP reasons")


# --------------------------------------------------------------------------- #
# governance lane
# --------------------------------------------------------------------------- #
def policy_compliance_checker(state: GraphState) -> dict:
    """CANDIDATE (Stretch): validate the decision/answer against policy rules
    (SPARQL/rules), apply PII redaction using OpenMetadata tags, set
    compliance_flags + requires_hitl."""
    raise NotImplementedError("TODO(candidate): policy + compliance checks")


# --------------------------------------------------------------------------- #
# output lane
# --------------------------------------------------------------------------- #
def explainer_citation(state: GraphState) -> dict:
    """PROVIDED reference: compose a grounded answer with citations from whatever
    lane produced results. Candidates may improve grounding/abstention."""
    parts, cites = [], []
    if state.get("rows"):
        parts.append(f"Found {state['row_count']} matching record(s).")
        cites.append({"type": "row", "source_id": "sql", "score": 1.0})
    if state.get("decision"):
        parts.append(f"Underwriting decision: {state['decision']} "
                     f"(PD={state.get('pd_score')}).")
        cites.append({"type": "policy", "source_id": "underwriting", "score": 1.0})
    for c in (state.get("contexts") or [])[:3]:
        parts.append(c.get("content", "")[:160])
        cites.append({"type": "document", "source_id": str(c.get("chunk_id", "")),
                      "page": c.get("page"), "score": c.get("rerank_score", 0.0)})
    if not parts:
        parts.append("I could not find this in the provided data.")
    return {"answer": " ".join(parts), "citations": cites,
            "confidence": state.get("confidence", 0.5)}
