# 1. Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-06-30
- **Deciders:** <you>

## Context

We need to capture the *significant* architecture decisions made while building
AutoLoan-DocIntel — not the code (git shows that), but the **why**: the options
weighed and the tradeoffs accepted. Reviewers grade engineering judgment, and
future-you needs to know why the system is the way it is.

## Decision

We use **Architecture Decision Records (ADRs)** — short Markdown files in
`docs/adr/`, numbered sequentially, one decision per file, append-only (a
superseded decision gets a new ADR that links back, rather than an edit).

This file is the template. Copy it for each new decision.

## Template

> **Status:** Proposed | Accepted | Superseded by [NNNN](NNNN-...md)
> **Date:** YYYY-MM-DD
> **Deciders:** names
>
> ### Context
> The forces at play: the problem, constraints (the firm stack, the contract,
> the 28h budget), and what makes this non-obvious.
>
> ### Options considered
> - **Option A** — pros / cons.
> - **Option B** — pros / cons.
> - **Option C** — pros / cons.
>
> ### Decision
> What we chose and the deciding reason.
>
> ### Consequences
> What gets better, what gets worse, what we now have to live with, and what
> would make us revisit this.

## Consequences

- Decisions are discoverable and reviewable in one place.
- The submission requires **≥ 3 ADRs**; this counts as one. Add at least two
  more for real decisions you made (two starters are provided: `0002`, `0003`).
- ADRs must match the code and commit history (see `SUBMISSION.md` anti-cheat).
