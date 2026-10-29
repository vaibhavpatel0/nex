"""Generic tool-calling agent loop for model providers (Ollama, OpenAI-compatible)."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.agents.base import AgentAdapter, AgentFailure, AgentOutput, AgentTask, AgentUnavailable, EmitFn, ToolClient
from app.integrations.providers.base import (
    ChatProvider,
    ChatResponse,
    ProviderError,
    ProviderUnavailable,
    from_wire_name,
    to_wire_name,
)
from app.security.redaction import redact_text

MAX_STEPS = 8


class LLMAgent(AgentAdapter):
    kind = "llm"

    def __init__(self, provider: ChatProvider, model: str, system_prompt: str) -> None:
        self.provider = provider
        self.model = model
        self.system_prompt = system_prompt

    async def check_available(self) -> "tuple[bool, str]":
        st = await self.provider.status(self.model)
        if st.get("provider") == "ollama":
            if not st.get("reachable"):
                return False, st.get("error", "Ollama not reachable")
            if not st.get("model_installed"):
                return False, f"Model {self.model} is not installed"
            if st.get("tool_support") is False:
                return False, f"Model {self.model} does not support tool calling"
            return True, "ready" if st.get("tool_support") else "ready (tool support unverified)"
        if not st.get("configured"):
            return False, "Hosted provider base URL not configured"
        if not st.get("api_key_present"):
            return False, f"Environment variable {st.get('api_key_env')} not set"
        return True, "configured"

    def _wire_args(self, args: Any) -> Any:
        # OpenAI-compatible APIs expect a JSON string; Ollama expects an object.
        if getattr(self.provider, "args_as_string", True):
            return args if isinstance(args, str) else json.dumps(args)
        return args if isinstance(args, dict) else {}

    def _tools_payload(self, tools: ToolClient) -> List[Dict[str, Any]]:
        return [
            {"type": "function", "function": {"name": to_wire_name(t.name), "description": t.description, "parameters": t.parameters}}
            for t in tools.available_tools()
        ]

    async def run(self, task: AgentTask, tools: ToolClient, emit: EmitFn) -> AgentOutput:
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": task.task},
        ]
        payload_tools = self._tools_payload(tools)
        usage_total: Optional[Dict[str, int]] = None
        calls = []
        fault = task.model_fault
        for step in range(1, MAX_STEPS + 1):
            try:
                resp: ChatResponse = await self.provider.chat(self.model, messages, payload_tools)
            except ProviderUnavailable as exc:
                raise AgentUnavailable(str(exc)) from exc
            except ProviderError as exc:
                raise AgentFailure(str(exc)) from exc
            if resp.usage:
                usage_total = usage_total or {"prompt_tokens": 0, "completion_tokens": 0}
                for k in usage_total:
                    usage_total[k] += int(resp.usage.get(k, 0))
            if fault == "malformed_tool_call" and resp.tool_calls:
                resp.tool_calls[0].arguments = "{not valid json"
                fault = None
            if fault == "empty_output" and not resp.tool_calls:
                resp.content = None
            emit("model.response", f"Model step {step}: {len(resp.tool_calls)} tool call(s)", {
                "simulated": False, "model": resp.model or self.model, "step": step,
                "tool_calls": [from_wire_name(c.name) for c in resp.tool_calls],
                "content_preview": redact_text(resp.content or "", 300) if resp.content else None,
                "usage": resp.usage,
            })
            if not resp.tool_calls:
                return AgentOutput(final_text=resp.content, usage=usage_total, steps=step, calls=calls)
            messages.append({
                "role": "assistant", "content": resp.content or "",
                "tool_calls": [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.name, "arguments": self._wire_args(c.arguments)}}
                    for c in resp.tool_calls
                ],
            })
            for c in resp.tool_calls:
                res = await tools.call(from_wire_name(c.name), c.arguments, origin="user_task")
                res.args = c.arguments if isinstance(c.arguments, dict) else {}
                calls.append(res)
                if res.status == "executed":
                    content = res.output_text or ""
                elif res.status == "pending_approval":
                    content = f"PENDING HUMAN APPROVAL ({res.approval_id}): {res.reason}. The action was not executed."
                elif res.status == "blocked":
                    content = f"BLOCKED BY SENTINEL ({res.matched_policy}): {res.reason}. The action was not executed."
                else:
                    content = f"TOOL FAILED ({res.error_class}). No result is available."
                if getattr(self.provider, "args_as_string", True):
                    # OpenAI-compatible APIs identify tool results by tool_call_id.
                    messages.append({"role": "tool", "tool_call_id": c.id, "content": content[:8000]})
                else:
                    # Ollama's native /api/chat identifies tool results by tool_name.
                    messages.append({"role": "tool", "tool_name": c.name, "content": content[:8000]})
        return AgentOutput(final_text=None, usage=usage_total, steps=MAX_STEPS, calls=calls)
