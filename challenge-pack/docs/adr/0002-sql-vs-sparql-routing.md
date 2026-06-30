# 2. SQL vs SPARQL routing

- **Status:** Proposed  <!-- TODO(candidate): set to Accepted and fill in -->
- **Date:** YYYY-MM-DD
- **Deciders:** <you>

> This is a **starter stub**. Replace the bracketed guidance with your real
> reasoning, options, and tradeoffs. Generic ADRs score low; we read these.

## Context

The `router_planner` node must pick a lane per question:
`sql | sparql | vector | underwrite | hybrid`. Two of these overlap in a
confusing way:

- **SQL** answers questions about *instances* in the relational warehouse
  (a specific application's DTI, payments on a loan, decisions in a branch).
- **SPARQL** answers questions about *policy structure / relationships* in the
  ontology (which rule supersedes which, regional applicability, rule lineage).

Many real questions are **hybrid** (e.g. "which approved loans violate the
superseded rate rule for the West region?" needs the ontology to resolve the rule
*and* SQL to find the loans). How the router decides — and how wrong routes are
recovered — drives both correctness and latency.

## Options considered

- **[Option A — LLM-only classifier]** Prompt the LLM to emit the route.
  *Pros:* simplest; flexible. *Cons:* [non-determinism, mis-routes, cost].
- **[Option B — rules/keywords first, LLM fallback]** Cheap heuristics
  (schema-term hits → SQL; policy/rule terms → SPARQL) then LLM for ambiguous.
  *Pros:* [fast, debuggable]. *Cons:* [brittle heuristics].
- **[Option C — always hybrid, let the critic prune]** Run SQL + SPARQL and let
  `aggregator_critic` reconcile. *Pros:* [recall]. *Cons:* [cost, latency].

## Decision

> **TODO(candidate):** state which you chose and the deciding reason. Describe
> how the SQL repair loop and/or the critic recovers a mis-route.

## Consequences

> **TODO(candidate):** what improves, what it costs (latency/tokens), and the
> failure modes you accept. What would make you revisit this?
