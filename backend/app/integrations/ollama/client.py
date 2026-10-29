"""Ollama provider (local models). Uses the documented REST API: /api/tags, /api/show, /api/chat.

Tool-calling support is verified per model from ``/api/show`` (``capabilities``). If the
installed Ollama version does not report capabilities, support is reported as
"unknown" and the first chat request decides; a refusal is surfaced as an ERROR.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import httpx

from app.config.settings import get_settings
from app.integrations.providers.base import ChatProvider, ChatResponse, ChatToolCall, ProviderError, ProviderUnavailable


class OllamaProvider(ChatProvider):
    name = "ollama"
    args_as_string = False

    def __init__(self, base_url: Optional[str] = None, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        s = get_settings()
        self.base_url = (base_url or s.ollama_base_url).rstrip("/")
        self.timeout = s.ollama_timeout_seconds
        self._transport = transport
        # Ollama Cloud (https://ollama.com) needs a Bearer key read from OLLAMA_API_KEY.
        # A local server needs no key; cloud models (":cloud" names) work through it after `ollama signin`.
        self.is_cloud = self.base_url.startswith("https://ollama.com")

    def _headers(self) -> Dict[str, str]:
        key = os.environ.get("OLLAMA_API_KEY")
        return {"Authorization": f"Bearer {key}"} if (self.is_cloud and key) else {}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout, transport=self._transport,
                                 headers=self._headers())

    async def status(self, model: Optional[str] = None) -> Dict[str, Any]:
        out: Dict[str, Any] = {"provider": "ollama", "base_url": self.base_url, "reachable": False,
                               "mode": "cloud" if self.is_cloud else "local",
                               "api_key_present": bool(os.environ.get("OLLAMA_API_KEY")) if self.is_cloud else None,
                               "models": [], "model": model, "model_installed": None, "tool_support": None}
        try:
            async with self._client() as c:
                r = await c.get("/api/tags", timeout=5)
                r.raise_for_status()
                out["reachable"] = True
                out["models"] = sorted(m.get("name", "") for m in r.json().get("models", []))
                if model:
                    out["model_installed"] = model in out["models"] or f"{model}:latest" in out["models"]
                    if out["model_installed"]:
                        s = await c.post("/api/show", json={"model": model}, timeout=10)
                        if s.status_code == 200:
                            caps = s.json().get("capabilities")
                            out["tool_support"] = ("tools" in caps) if isinstance(caps, list) else "unknown"
        except (httpx.HTTPError, ValueError) as exc:
            out["error"] = f"{type(exc).__name__}: could not reach Ollama at {self.base_url}"
        return out

    async def chat(self, model: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> ChatResponse:
        payload = {"model": model, "messages": messages, "tools": tools, "stream": False, "options": {"temperature": 0}}
        try:
            async with self._client() as c:
                r = await c.post("/api/chat", json=payload)
        except httpx.ConnectError as exc:
            raise ProviderUnavailable(f"Ollama is not reachable at {self.base_url}") from exc
        except httpx.TimeoutException as exc:
            raise ProviderUnavailable("Ollama request timed out") from exc
        if r.status_code in (401, 403):
            raise ProviderUnavailable("Ollama rejected the request: sign in (ollama signin) or set OLLAMA_API_KEY for cloud models")
        if r.status_code == 404:
            raise ProviderUnavailable(f"Model '{model}' is not installed in Ollama (run: ollama pull {model})")
        if r.status_code >= 400:
            detail = r.text[:200]
            if "does not support tools" in detail:
                raise ProviderUnavailable(f"Model '{model}' does not support tool calling")
            raise ProviderError(f"Ollama returned HTTP {r.status_code}")
        try:
            body = r.json()
        except ValueError as exc:
            raise ProviderError("Ollama returned invalid JSON") from exc
        msg = body.get("message") or {}
        calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    pass
            calls.append(ChatToolCall(id=f"call_{i}", name=str(fn.get("name", "")), arguments=args))
        usage = None
        if "prompt_eval_count" in body or "eval_count" in body:
            usage = {"prompt_tokens": int(body.get("prompt_eval_count", 0)), "completion_tokens": int(body.get("eval_count", 0))}
        content = msg.get("content")
        return ChatResponse(content=content if content else None, tool_calls=calls, usage=usage, model=body.get("model"))
