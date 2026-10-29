"""Optional hosted provider using the OpenAI-compatible Chat Completions format.

Works with any service that implements ``POST {base_url}/chat/completions`` with tools
(for example OpenAI, Groq, OpenRouter, Together, or LM Studio locally). The API key is
read at call time from the environment variable named by ``AEGIS_HOSTED_API_KEY_ENV``;
it is never stored in the database, returned by the API, or logged.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import httpx

from app.config.settings import get_settings
from app.integrations.providers.base import ChatProvider, ChatResponse, ChatToolCall, ProviderError, ProviderUnavailable


class OpenAICompatibleProvider(ChatProvider):
    name = "openai_compatible"
    args_as_string = True

    def __init__(self, base_url: Optional[str] = None, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        s = get_settings()
        self.base_url = (base_url or s.hosted_base_url).rstrip("/")
        self.key_env = s.hosted_api_key_env
        self.timeout = s.hosted_timeout_seconds
        self._transport = transport

    def _key(self) -> Optional[str]:
        return os.environ.get(self.key_env) or None

    async def status(self, model: Optional[str] = None) -> Dict[str, Any]:
        return {
            "provider": "openai_compatible",
            "configured": bool(self.base_url),
            "base_url": self.base_url or None,
            "api_key_env": self.key_env,
            "api_key_present": self._key() is not None,
            "model": model,
            "note": "Connectivity is checked on first use; no request is sent by the status check.",
        }

    async def chat(self, model: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> ChatResponse:
        if not self.base_url:
            raise ProviderUnavailable("AEGIS_HOSTED_BASE_URL is not configured")
        key = self._key()
        if key is None:
            raise ProviderUnavailable(f"Environment variable {self.key_env} is not set")
        payload = {"model": model, "messages": messages, "tools": tools, "temperature": 0}
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as c:
                r = await c.post(f"{self.base_url}/chat/completions", json=payload,
                                 headers={"Authorization": f"Bearer {key}"})
        except httpx.ConnectError as exc:
            raise ProviderUnavailable("Hosted provider is not reachable") from exc
        except httpx.TimeoutException as exc:
            raise ProviderUnavailable("Hosted provider request timed out") from exc
        if r.status_code in (401, 403):
            raise ProviderUnavailable(f"Hosted provider rejected the credentials (HTTP {r.status_code})")
        if r.status_code == 429:
            raise ProviderUnavailable("Hosted provider rate limit reached (HTTP 429)")
        if r.status_code >= 400:
            raise ProviderError(f"Hosted provider returned HTTP {r.status_code}")
        try:
            body = r.json()
            msg = body["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Hosted provider returned an unexpected payload") from exc
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            raw = fn.get("arguments")
            try:
                args: Any = json.loads(raw) if isinstance(raw, str) else raw
            except ValueError:
                args = raw  # malformed JSON from the model: SENTINEL will reject it
            calls.append(ChatToolCall(id=str(tc.get("id", "")), name=str(fn.get("name", "")), arguments=args))
        usage = None
        if isinstance(body.get("usage"), dict):
            u = body["usage"]
            usage = {"prompt_tokens": int(u.get("prompt_tokens", 0)), "completion_tokens": int(u.get("completion_tokens", 0))}
        return ChatResponse(content=msg.get("content") or None, tool_calls=calls, usage=usage, model=body.get("model"))
