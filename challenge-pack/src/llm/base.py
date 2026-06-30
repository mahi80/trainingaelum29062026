"""Pluggable LLM interface. Default impl talks to a local Ollama server.

Swap the implementation (vLLM, llama.cpp, a hosted API) without touching nodes —
everything depends only on the `LLM` Protocol.
"""
from __future__ import annotations
import json
import os
from typing import Protocol, runtime_checkable


@runtime_checkable
class LLM(Protocol):
    def complete(self, prompt: str, *, system: str | None = None,
                 as_json: bool = False, stop: list[str] | None = None,
                 temperature: float = 0.0) -> str: ...

    def stream(self, prompt: str, *, system: str | None = None,
               temperature: float = 0.0): ...  # -> Iterator[str]

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaLLM:
    """Local Ollama (http://ollama:11434). Models pulled by the compose init job."""

    def __init__(self, model: str | None = None, embed_model: str | None = None,
                 host: str | None = None):
        self.host = host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        self.model = model or os.environ.get("LLM_MODEL", "llama3.1:8b")
        self.embed_model = embed_model or os.environ.get("EMBED_MODEL", "nomic-embed-text")

    def _post(self, path: str, payload: dict) -> dict:
        import requests
        r = requests.post(self.host + path, json=payload, timeout=120)
        r.raise_for_status()
        return r.json()

    def complete(self, prompt, *, system=None, as_json=False, stop=None, temperature=0.0):
        payload = {"model": self.model, "prompt": prompt, "stream": False,
                   "options": {"temperature": temperature, "stop": stop or []}}
        if system:
            payload["system"] = system
        if as_json:
            payload["format"] = "json"
        return self._post("/api/generate", payload).get("response", "")

    def stream(self, prompt, *, system=None, temperature=0.0):
        import requests
        payload = {"model": self.model, "prompt": prompt, "stream": True,
                   "options": {"temperature": temperature}}
        if system:
            payload["system"] = system
        with requests.post(self.host + "/api/generate", json=payload,
                           stream=True, timeout=300) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                tok = json.loads(line).get("response", "")
                if tok:
                    yield tok

    def embed(self, texts):
        out = []
        for t in texts:
            res = self._post("/api/embeddings", {"model": self.embed_model, "prompt": t})
            out.append(res["embedding"])
        return out


def get_llm() -> LLM:
    """Factory honoring env (LLM_PROVIDER=ollama by default)."""
    provider = os.environ.get("LLM_PROVIDER", "ollama").lower()
    if provider == "ollama":
        return OllamaLLM()
    raise ValueError(f"unknown LLM_PROVIDER={provider}")
