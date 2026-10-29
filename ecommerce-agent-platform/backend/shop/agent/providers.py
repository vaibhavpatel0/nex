"""Model providers for the shopping agent.

- OllamaProvider: Ollama's native /api/chat with tools. Works with a local server
  (http://127.0.0.1:11434, no key) or Ollama's cloud API (https://ollama.com, Bearer OLLAMA_API_KEY).
  Tool results are sent back as {"role": "tool", "tool_name": ..., "content": ...} per the Ollama docs.
- OpenAICompatibleProvider: POST {base}/chat/completions with tools (OpenAI, Groq, OpenRouter, LM Studio...).
- FaultInjectingProvider: wraps a provider in sandbox test runs only. Every injected fault is labelled
  in the trace; with fault "timeout" no request is sent to the model at all.

The API key is read from settings at call time and only ever placed in the Authorization header.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from shop.config import get_settings


class ModelTimeout(Exception):
    pass


class ModelUnavailable(Exception):
    pass


class ModelError(Exception):
    pass


@dataclass
class ProposedCall:
    id: str
    name: str
    arguments: Any  # dict when parseable; raw value otherwise (SENTINEL rejects it)


@dataclass
class ModelReply:
    content: Optional[str]
    calls: List[ProposedCall] = field(default_factory=list)
    usage: Optional[Dict[str, int]] = None
    model: Optional[str] = None
    done_reason: Optional[str] = None
    duration_ms: float = 0.0
    injected_fault: Optional[str] = None


class Provider:
    name = "abstract"
    args_as_string = False

    @property
    def model(self) -> str:
        raise NotImplementedError

    async def chat(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> ModelReply:
        raise NotImplementedError

    def assistant_message(self, reply: ModelReply) -> Dict[str, Any]:
        raise NotImplementedError

    def tool_message(self, call: ProposedCall, content: str) -> Dict[str, Any]:
        raise NotImplementedError

    async def status(self) -> Dict[str, Any]:
        raise NotImplementedError


class OllamaProvider(Provider):
    name = "ollama"

    def __init__(self, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self._transport = transport

    @property
    def model(self) -> str:
        return get_settings().ollama_model

    def _client(self, timeout: float) -> httpx.AsyncClient:
        s = get_settings()
        headers = {}
        if s.ollama_api_key and s.ollama_is_cloud:
            headers["Authorization"] = f"Bearer {s.ollama_api_key}"
        return httpx.AsyncClient(base_url=s.ollama_base_url.rstrip("/"), timeout=timeout, transport=self._transport, headers=headers)

    async def chat(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> ModelReply:
        s = get_settings()
        payload = {"model": self.model, "messages": messages, "tools": tools, "stream": False, "options": {"temperature": 0}}
        t0 = time.perf_counter()
        try:
            async with self._client(s.model_timeout_seconds) as c:
                r = await c.post("/api/chat", json=payload)
        except httpx.TimeoutException as exc:
            raise ModelTimeout(f"No response from the model within {s.model_timeout_seconds:g}s") from exc
        except httpx.HTTPError as exc:
            raise ModelUnavailable(f"Ollama not reachable at {s.ollama_base_url}") from exc
        ms = (time.perf_counter() - t0) * 1000
        if r.status_code in (401, 403):
            raise ModelUnavailable("Ollama rejected the credentials: check OLLAMA_API_KEY (cloud) or run `ollama signin`")
        if r.status_code == 404:
            raise ModelUnavailable(f"Model '{self.model}' not found (local: `ollama pull {self.model}`; cloud: check the name)")
        if r.status_code == 429:
            raise ModelUnavailable("Ollama rate or usage limit reached (HTTP 429)")
        if r.status_code >= 400:
            detail = ""
            try:
                detail = str(r.json().get("error", ""))[:200]
            except ValueError:
                pass
            if "does not support tools" in detail:
                raise ModelUnavailable(f"Model '{self.model}' does not support tool calling")
            raise ModelError(f"Ollama returned HTTP {r.status_code}" + (f": {detail}" if detail else ""))
        try:
            body = r.json()
        except ValueError as exc:
            raise ModelError("Ollama returned invalid JSON") from exc
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
            calls.append(ProposedCall(id=str(tc.get("id") or f"call_{i}"), name=str(fn.get("name", "")), arguments=args))
        usage = None
        if "prompt_eval_count" in body or "eval_count" in body:
            usage = {"prompt_tokens": int(body.get("prompt_eval_count") or 0), "completion_tokens": int(body.get("eval_count") or 0)}
        return ModelReply(content=(msg.get("content") or None), calls=calls, usage=usage, model=body.get("model") or self.model,
                          done_reason=body.get("done_reason"), duration_ms=ms)

    def assistant_message(self, reply: ModelReply) -> Dict[str, Any]:
        return {"role": "assistant", "content": reply.content or "",
                "tool_calls": [{"type": "function", "function": {"name": c.name,
                                                                 "arguments": c.arguments if isinstance(c.arguments, dict) else {}}}
                               for c in reply.calls]}

    def tool_message(self, call: ProposedCall, content: str) -> Dict[str, Any]:
        return {"role": "tool", "tool_name": call.name, "content": content}

    async def status(self) -> Dict[str, Any]:
        s = get_settings()
        out: Dict[str, Any] = {"provider": "ollama", "mode": "cloud" if s.ollama_is_cloud else "local", "base_url": s.ollama_base_url,
                               "model": self.model, "api_key_configured": bool(s.ollama_api_key), "reachable": False,
                               "model_listed": None, "tool_support": None, "models": []}
        try:
            async with self._client(8) as c:
                r = await c.get("/api/tags")
                r.raise_for_status()
                out["reachable"] = True
                names = sorted(m.get("name") or m.get("model") or "" for m in r.json().get("models", []))
                out["models"] = names[:80]
                out["model_listed"] = self.model in names or f"{self.model}:latest" in names
                sh = await c.post("/api/show", json={"model": self.model})
                if sh.status_code == 200:
                    caps = sh.json().get("capabilities")
                    out["tool_support"] = ("tools" in caps) if isinstance(caps, list) else "not reported"
                else:
                    out["show_status"] = sh.status_code
        except httpx.HTTPError as exc:
            out["error"] = f"{type(exc).__name__}: could not reach {s.ollama_base_url}"
        except ValueError:
            out["error"] = "Unexpected response from Ollama"
        return out


class OpenAICompatibleProvider(Provider):
    name = "openai_compatible"
    args_as_string = True

    def __init__(self, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self._transport = transport

    @property
    def model(self) -> str:
        return get_settings().openai_model

    async def chat(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> ModelReply:
        s = get_settings()
        if not s.openai_base_url or not s.openai_api_key:
            raise ModelUnavailable("SHOP_OPENAI_BASE_URL and SHOP_OPENAI_API_KEY must be set")
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=s.model_timeout_seconds, transport=self._transport) as c:
                r = await c.post(s.openai_base_url.rstrip("/") + "/chat/completions",
                                 json={"model": self.model, "messages": messages, "tools": tools, "temperature": 0},
                                 headers={"Authorization": f"Bearer {s.openai_api_key}"})
        except httpx.TimeoutException as exc:
            raise ModelTimeout(f"No response from the model within {s.model_timeout_seconds:g}s") from exc
        except httpx.HTTPError as exc:
            raise ModelUnavailable("Provider not reachable") from exc
        if r.status_code in (401, 403, 404, 429):
            raise ModelUnavailable(f"Provider returned HTTP {r.status_code}")
        if r.status_code >= 400:
            raise ModelError(f"Provider returned HTTP {r.status_code}")
        try:
            body = r.json()
            msg = body["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelError("Unexpected provider payload") from exc
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            raw = fn.get("arguments")
            try:
                args: Any = json.loads(raw) if isinstance(raw, str) else raw
            except ValueError:
                args = raw
            calls.append(ProposedCall(id=str(tc.get("id", "")), name=str(fn.get("name", "")), arguments=args))
        u = body.get("usage") if isinstance(body.get("usage"), dict) else None
        usage = {"prompt_tokens": int(u.get("prompt_tokens", 0)), "completion_tokens": int(u.get("completion_tokens", 0))} if u else None
        return ModelReply(content=msg.get("content") or None, calls=calls, usage=usage, model=body.get("model") or self.model,
                          done_reason=body["choices"][0].get("finish_reason"), duration_ms=(time.perf_counter() - t0) * 1000)

    def assistant_message(self, reply: ModelReply) -> Dict[str, Any]:
        return {"role": "assistant", "content": reply.content or "",
                "tool_calls": [{"id": c.id, "type": "function",
                                "function": {"name": c.name, "arguments": c.arguments if isinstance(c.arguments, str) else json.dumps(c.arguments)}}
                               for c in reply.calls]}

    def tool_message(self, call: ProposedCall, content: str) -> Dict[str, Any]:
        return {"role": "tool", "tool_call_id": call.id, "content": content}

    async def status(self) -> Dict[str, Any]:
        s = get_settings()
        return {"provider": "openai_compatible", "base_url": s.openai_base_url or None, "model": self.model,
                "api_key_configured": bool(s.openai_api_key), "reachable": None,
                "note": "Checked on first use; the status view sends no request to this provider."}


class FaultInjectingProvider(Provider):
    """Sandbox-only wrapper. Faults: model 'timeout' (no request sent), model_fault 'malformed_tool_call'
    (the first proposed call's arguments are corrupted before SENTINEL sees them), 'empty_output'."""

    def __init__(self, inner: Provider, model_fault_mode: Optional[str], model_fault: Optional[str]) -> None:
        self.inner = inner
        self.timeout_mode = model_fault_mode
        self.model_fault = model_fault
        self.name = inner.name
        self.args_as_string = inner.args_as_string

    @property
    def model(self) -> str:
        return self.inner.model

    async def chat(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> ModelReply:
        if self.timeout_mode == "timeout":
            await asyncio.sleep(0.05)
            raise ModelTimeout("Injected fault: the model call exceeded its timeout (no request was sent to the model)")
        reply = await self.inner.chat(messages, tools)
        if self.model_fault == "malformed_tool_call" and reply.calls:
            c = reply.calls[0]
            bad = {k: 5002 for k in (c.arguments or {})} if isinstance(c.arguments, dict) else {}
            bad["unexpected_field"] = "injected-by-model-fault"
            c.arguments = bad
            reply.injected_fault = "malformed_tool_call"
            self.model_fault = None
        elif self.model_fault == "empty_output" and not reply.calls:
            reply.content = None
            reply.injected_fault = "empty_output"
        return reply

    def assistant_message(self, reply: ModelReply) -> Dict[str, Any]:
        return self.inner.assistant_message(reply)

    def tool_message(self, call: ProposedCall, content: str) -> Dict[str, Any]:
        return self.inner.tool_message(call, content)

    async def status(self) -> Dict[str, Any]:
        return await self.inner.status()


def build_provider(transport: Optional[httpx.AsyncBaseTransport] = None) -> Provider:
    kind = get_settings().model_provider
    if kind == "openai_compatible":
        return OpenAICompatibleProvider(transport)
    return OllamaProvider(transport)
