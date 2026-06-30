"""Assemble the 14-node LangGraph.

Wiring + conditional routing + repair/critic loops + HITL interrupt are provided.
Candidates implement the node bodies (see src/nodes/nodes.py). The compiled graph
uses a Redis checkpointer so HITL interrupts survive a pause/resume.

build_graph() returns a compiled graph; if langgraph/redis aren't importable the
import simply fails at call time — the wiring below is the contract.
"""
from __future__ import annotations
import os

from .state import GraphState
from .. import nodes as N

MAX_REPAIR = 3
MAX_CRITIC = 2


def _route_from_planner(state: GraphState) -> str:
    return state.get("route", "vector")


def _after_validate(state: GraphState) -> str:
    if state.get("sql_valid"):
        return "sql_executor"
    if state.get("repair_attempts", 0) >= MAX_REPAIR:
        return "aggregator_critic"   # give up gracefully (degraded)
    return "nl2sql_generator"        # repair loop


def _after_critic(state: GraphState) -> str:
    if state.get("critic_retries", 0) >= MAX_CRITIC:
        return "explainer_citation"
    return "explainer_citation" if not state.get("degraded") else "explainer_citation"


def _needs_hitl(state: GraphState) -> str:
    return "hitl_gate" if state.get("requires_hitl") else "aggregator_critic"


def build_graph(checkpointer=None):
    from langgraph.graph import StateGraph, START, END

    g = StateGraph(GraphState)
    for name, fn in [
        ("memory", N.memory), ("router_planner", N.router_planner),
        ("schema_linker", N.schema_linker), ("nl2sql_generator", N.nl2sql_generator),
        ("sql_validator_repair", N.sql_validator_repair), ("sql_executor", N.sql_executor),
        ("sparql_ontology_agent", N.sparql_ontology_agent),
        ("vector_retriever", N.vector_retriever), ("reranker", N.reranker),
        ("underwriting_scorer", N.underwriting_scorer),
        ("policy_compliance_checker", N.policy_compliance_checker),
        ("hitl_gate", N.hitl_gate), ("aggregator_critic", N.aggregator_critic),
        ("explainer_citation", N.explainer_citation),
    ]:
        g.add_node(name, fn)

    g.add_edge(START, "memory")
    g.add_edge("memory", "router_planner")

    # route -> lane entry points
    g.add_conditional_edges("router_planner", _route_from_planner, {
        "sql": "schema_linker",
        "sparql": "sparql_ontology_agent",
        "vector": "vector_retriever",
        "underwrite": "underwriting_scorer",
        "hybrid": "schema_linker",
    })

    # sql lane (with repair loop)
    g.add_edge("schema_linker", "nl2sql_generator")
    g.add_edge("nl2sql_generator", "sql_validator_repair")
    g.add_conditional_edges("sql_validator_repair", _after_validate, {
        "sql_executor": "sql_executor",
        "nl2sql_generator": "nl2sql_generator",
        "aggregator_critic": "aggregator_critic",
    })
    g.add_edge("sql_executor", "aggregator_critic")

    # sparql lane
    g.add_edge("sparql_ontology_agent", "aggregator_critic")

    # retrieval lane
    g.add_edge("vector_retriever", "reranker")
    g.add_edge("reranker", "aggregator_critic")

    # underwriting lane -> compliance -> (HITL?) -> critic
    g.add_edge("underwriting_scorer", "policy_compliance_checker")
    g.add_conditional_edges("policy_compliance_checker", _needs_hitl, {
        "hitl_gate": "hitl_gate", "aggregator_critic": "aggregator_critic",
    })
    g.add_edge("hitl_gate", "aggregator_critic")

    # converge
    g.add_conditional_edges("aggregator_critic", _after_critic, {
        "explainer_citation": "explainer_citation",
    })
    g.add_edge("explainer_citation", END)

    if checkpointer is None:
        checkpointer = _redis_checkpointer()
    return g.compile(checkpointer=checkpointer,
                     interrupt_before=["hitl_gate"])


def _redis_checkpointer():
    """Redis-backed checkpointer so HITL interrupts are durable across restarts."""
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    try:
        from langgraph.checkpoint.redis import RedisSaver
        return RedisSaver.from_conn_string(url)
    except Exception:
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()
