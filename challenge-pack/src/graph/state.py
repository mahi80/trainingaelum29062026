"""Shared LangGraph state threaded through all 14 nodes."""
from __future__ import annotations
from typing import Literal, TypedDict


class Citation(TypedDict, total=False):
    source_id: str
    type: str
    page: int
    score: float


class GraphState(TypedDict, total=False):
    # io
    question: str
    session_id: str
    answer: str
    citations: list[Citation]
    # routing / control
    route: Literal["sql", "sparql", "vector", "underwrite", "hybrid"]
    plan: list[str]
    repair_attempts: int
    critic_retries: int
    degraded: bool
    confidence: float
    # schema linking + sql lane
    linked_tables: list[str]
    linked_columns: list[str]
    join_hints: list[str]
    sql_draft: str
    sql_valid: str
    sql_error: str | None
    rows: list[dict]
    row_count: int
    truncated: bool
    # sparql lane
    sparql: str
    graph_rows: list[dict]
    # retrieval lane
    candidates: list[dict]      # pre-rerank
    contexts: list[dict]        # post-rerank
    retrieval_scores: dict
    # underwriting
    applicant_features: dict
    decision: Literal["approve", "refer", "deny"]
    pd_score: float
    pd_confidence: float
    reason_codes: list[dict]
    # compliance / hitl
    compliance_flags: list[str]
    requires_hitl: bool
    human_override: dict | None
    # memory / observability
    memory_summary: str
    entities: dict
    trace: list[dict]
