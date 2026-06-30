# 3. Redis Streams vs a queue for ingestion

- **Status:** Proposed  <!-- TODO(candidate): set to Accepted and fill in -->
- **Date:** YYYY-MM-DD
- **Deciders:** <you>

> This is a **starter stub**. Replace the bracketed guidance with your real
> reasoning, options, and tradeoffs.

## Context

`POST /v1/ingest` returns **202 `{job_id}`** and processes documents
asynchronously (OCR → chunk → embed → upsert into `doc.document_chunk`).
`GET /v1/ingest/{job_id}` reports progress. The firm stack already runs **Redis
7**, and the contract description names **Redis Streams** as the backing
mechanism. We need: durable hand-off, at-least-once processing, progress
tracking (`processed`/`total`), and crash recovery without losing jobs.

## Options considered

- **[Option A — Redis Streams + consumer groups]** `XADD` to enqueue, a worker
  reads via a consumer group, `XACK` on success, `XAUTOCLAIM`/PEL for retries.
  *Pros:* [already in stack; durable; built-in pending/ack/replay; per-job state
  in a Redis hash]. *Cons:* [you build the worker loop; trimming/retention to
  manage].
- **[Option B — a dedicated broker (RabbitMQ / SQS-like)]**
  *Pros:* [mature delivery semantics, DLQ]. *Cons:* [new service, violates
  OSS/local-first lean stack, more ops].
- **[Option C — Celery/RQ on Redis]** *Pros:* [batteries-included tasks].
  *Cons:* [heavier dep; abstracts away the Streams semantics we want to show].
- **[Option D — DB table as a queue (`SELECT ... FOR UPDATE SKIP LOCKED`)]**
  *Pros:* [transactional with the writes]. *Cons:* [polling; reinvents a broker].

## Decision

> **TODO(candidate):** state which you chose (the contract steers toward Streams)
> and why. Describe idempotency (`Idempotency-Key`), retry/visibility handling,
> and where `job_id` status lives.

## Consequences

> **TODO(candidate):** delivery guarantees you provide (at-least-once?), how
> duplicates are made safe (idempotent upserts), backpressure/rate-limit
> interaction, and what you'd change at higher volume.
