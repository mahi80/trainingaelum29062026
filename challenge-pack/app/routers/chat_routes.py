"""Chat console (HTMX page) + POST /v1/chat (SSE streaming).

  * ``GET /chat``        PROVIDED — renders the HTMX console (auth required).
  * ``POST /v1/chat``    PROVIDED transport (SSE framing, heartbeats, error wrapping)
                         + CANDIDATE wiring point for real graph output.

The contract (openapi/contract.yaml) says: when ``stream=true`` return an SSE
stream (``text/event-stream``); otherwise return a JSON ChatResponse. This module
implements the *transport*: it builds the LangGraph via ``src.graph.build_graph``,
streams events, sends periodic heartbeats so proxies don't time out, and — because
several graph nodes are intentionally ``NotImplementedError`` in the starter — it
catches that and streams a clear "not implemented yet" SSE event so the page still
works end-to-end.

TODO(candidate): replace the placeholder token/citation emission inside
``_run_graph_stream`` with real streaming from your implemented nodes (token
deltas from the explainer, the chosen ``route``, generated ``sql``, ``citations``,
and final ``decision``). The SSE event names below are the contract the HTMX page
(chat.html, htmx-sse) already listens for: ``token``, ``meta``, ``citation``,
``error``, ``done``.
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..auth.deps import get_current_user
from ..templating import templates

router = APIRouter(tags=["chat"])

HEARTBEAT_SECONDS = 15.0


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: Optional[str] = None
    stream: bool = True


def _sse(event: str, data) -> str:
    """Format a Server-Sent Event frame. ``data`` is JSON-encoded."""
    payload = data if isinstance(data, str) else json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


# ---------------------------------------------------------------------------
# GET /chat — HTMX console (PROVIDED)
# ---------------------------------------------------------------------------
@router.get("/chat", response_class=HTMLResponse, name="chat_console")
async def chat_console(request: Request, user: dict = Depends(get_current_user)):
    return templates.TemplateResponse(
        request, "chat.html", {"user": user}
    )


# ---------------------------------------------------------------------------
# Graph streaming — transport PROVIDED, output is the CANDIDATE wiring point
# ---------------------------------------------------------------------------
async def _run_graph_stream(question: str, session_id: str) -> AsyncIterator[str]:
    """Yield SSE frames for a single chat turn.

    PROVIDED transport with a CANDIDATE wiring point. We attempt to build and run
    the real graph; any ``NotImplementedError`` from an unfinished node is turned
    into a friendly SSE ``error`` event so the UI keeps working during development.
    """
    from ..middleware.request_context import get_correlation_id

    yield _sse("meta", {"session_id": session_id, "correlation_id": get_correlation_id()})

    try:
        # Lazy import so the module loads without langgraph/redis installed.
        from src.graph import build_graph

        graph = build_graph()
        config = {"configurable": {"thread_id": session_id}}
        initial = {"question": question, "session_id": session_id}

        # TODO(candidate): switch to token-level streaming once nodes emit deltas,
        # e.g. `async for chunk in graph.astream(initial, config, stream_mode="messages")`.
        # The starter uses a coarse state stream and emits the final answer/citations.
        final_state: dict = {}
        last_beat = asyncio.get_event_loop().time()

        for step in graph.stream(initial, config, stream_mode="values"):
            final_state = step or final_state
            now = asyncio.get_event_loop().time()
            if now - last_beat >= HEARTBEAT_SECONDS:
                yield ": heartbeat\n\n"  # SSE comment frame keeps the socket warm
                last_beat = now
            await asyncio.sleep(0)  # cooperative yield

        route = final_state.get("route")
        sql = final_state.get("sql_draft") or final_state.get("sql")
        if route or sql:
            yield _sse("meta", {"route": route, "sql": sql})

        answer = final_state.get("answer") or ""
        # TODO(candidate): stream real token deltas here instead of whole answer.
        for piece in _chunk_text(answer):
            yield _sse("token", piece)
            await asyncio.sleep(0)

        for citation in final_state.get("citations", []) or []:
            yield _sse("citation", citation)

        if final_state.get("decision"):
            yield _sse("meta", {"decision": final_state["decision"]})

        if not answer:
            yield _sse(
                "token",
                "(graph produced no answer yet — implement the nodes to see output)",
            )

    except NotImplementedError as exc:
        node = str(exc) or "a graph node"
        yield _sse(
            "error",
            {
                "message": f"Not implemented yet: {node}. "
                "Implement the LangGraph nodes in src/nodes/nodes.py to enable "
                "real answers.",
                "kind": "not_implemented",
            },
        )
    except Exception as exc:  # transport must not crash the stream
        yield _sse("error", {"message": f"chat failed: {exc}", "kind": "internal"})

    yield _sse("done", {"ok": True})


def _chunk_text(text: str, size: int = 24):
    for i in range(0, len(text), size):
        yield text[i : i + size]


# ---------------------------------------------------------------------------
# POST /v1/chat — contract endpoint (PROVIDED transport)
# ---------------------------------------------------------------------------
@router.post("/v1/chat", name="chat")
async def chat(
    request: Request,
    body: ChatRequest,
    user: dict = Depends(get_current_user),
):
    session_id = body.session_id or user.get("session_id") or "anon"

    if body.stream:
        return StreamingResponse(
            _run_graph_stream(body.message, session_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # disable nginx buffering for SSE
                "Connection": "keep-alive",
            },
        )

    # Non-streaming JSON path (ChatResponse shape from the contract).
    # TODO(candidate): replace with the final graph state once nodes are implemented.
    answer, route, sql, citations, decision = await _run_graph_once(
        body.message, session_id
    )
    return JSONResponse(
        {
            "answer": answer,
            "route": route,
            "sql": sql,
            "citations": citations,
            "decision": decision,
            "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        }
    )


async def _run_graph_once(question: str, session_id: str):
    """Run the graph to completion and return a ChatResponse tuple.

    PROVIDED scaffold; CANDIDATE wires the real state fields. Falls back to a
    clear placeholder answer when nodes are unimplemented so /v1/chat?stream=false
    still returns a valid 200.
    """
    try:
        from src.graph import build_graph

        graph = build_graph()
        config = {"configurable": {"thread_id": session_id}}
        state = graph.invoke({"question": question, "session_id": session_id}, config)
        return (
            state.get("answer", ""),
            state.get("route"),
            state.get("sql_draft") or state.get("sql"),
            state.get("citations", []) or [],
            state.get("decision"),
        )
    except NotImplementedError as exc:
        return (
            f"Not implemented yet: {exc}. Implement the LangGraph nodes to enable "
            "real answers.",
            None,
            None,
            [],
            None,
        )
    except Exception as exc:
        return (f"chat failed: {exc}", None, None, [], None)
