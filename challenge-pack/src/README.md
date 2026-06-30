# `src/` — agent core (LangGraph skeleton + LLM interface)

The orchestration skeleton you build on. The **wiring is provided**; you
implement the node **bodies**.

```
src/llm/      pluggable LLM interface (Protocol) + Ollama implementation  [PROVIDED]
src/graph/    state.py (GraphState) + build.py (14-node wiring)           [PROVIDED]
src/nodes/    the 14 nodes — CANDIDATE bodies raise NotImplementedError    [you build]
```

## The 14 nodes (lanes)
| Node | Lane | Who |
|------|------|-----|
| router_planner | control | candidate |
| schema_linker · nl2sql_generator · sql_validator_repair | sql | candidate |
| sql_executor | sql | provided-stub |
| sparql_ontology_agent | graph | candidate (stretch) |
| vector_retriever · reranker | rag | candidate |
| underwriting_scorer | ml | candidate |
| policy_compliance_checker | governance | candidate (stretch) |
| hitl_gate | control | candidate (stretch) |
| aggregator_critic | control | candidate |
| explainer_citation | output | provided reference |
| memory | state | provided-stub |

## Routing & loops (in `build.py`)
`router_planner` picks `sql | sparql | vector | underwrite | hybrid`. The SQL lane
has a **repair loop** (`nl2sql → validate → repair`, ≤3). The underwriting lane
runs `score → policy/compliance → (HITL if flagged) → critic`. `aggregator_critic`
can retry (≤2). The graph compiles with a **Redis checkpointer** and
`interrupt_before=["hitl_gate"]` so human review is durable.

## LLM
Everything depends only on `src.llm.LLM` (Protocol). Default `OllamaLLM` talks to
`$OLLAMA_HOST`. `get_llm()` honors `$LLM_PROVIDER`. Embeddings are 768-d
(`nomic-embed-text`) to match the pgvector columns.

## Run the (stub) graph
```python
from src.graph import build_graph
graph = build_graph()
# graph.invoke({"question": "...", "session_id": "s1"}, config={"configurable": {"thread_id": "s1"}})
```
Build the SQL vertical slice first (router → schema_linker → nl2sql → validate →
executor → explainer), then add lanes. See `grading-kit/rubric.md` for tiers.
