"""Build an agent adapter from a stored agent configuration."""
from __future__ import annotations

from typing import Any, Dict

from app.agents.base import AgentAdapter, AgentUnavailable
from app.agents.llm_agent import LLMAgent
from app.agents.simulated import SimulatedAgent
from app.integrations.ollama.client import OllamaProvider
from app.integrations.providers.openai_compatible import OpenAICompatibleProvider
from app.seed.catalog import DEFAULT_LLM_PROMPT

SUPPORTED = {
    ("simulated", "deterministic"),
    ("local_model", "ollama"),
    ("hosted_model", "openai_compatible"),
}


def build_agent(snapshot: Dict[str, Any]) -> AgentAdapter:
    kind = (snapshot.get("agent_type"), snapshot.get("provider"))
    if kind not in SUPPORTED:
        raise AgentUnavailable(f"No adapter is implemented for {kind[0]} / {kind[1]}")
    if kind[0] == "simulated":
        return SimulatedAgent(snapshot.get("behavior") or "naive")
    model = snapshot.get("model_id")
    if not model:
        raise AgentUnavailable("No model identifier configured")
    prompt = snapshot.get("system_prompt") or DEFAULT_LLM_PROMPT
    provider = OllamaProvider() if kind[1] == "ollama" else OpenAICompatibleProvider()
    return LLMAgent(provider, model, prompt)
