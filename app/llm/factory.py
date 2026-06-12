"""
LLM provider factory. Returns the configured provider from LLM_PROVIDER env var.
"""

from __future__ import annotations

from app.config import settings
from app.llm.base import LLMProvider


def get_llm_provider(model: str | None = None) -> LLMProvider:
    """
    Returns an LLMProvider for the configured backend.
    model defaults to settings.agent_model; pass an override for eval runs.
    """
    resolved_model = model or settings.agent_model

    provider = settings.llm_provider.lower()
    if provider == "anthropic":
        from app.llm.anthropic_client import AnthropicProvider
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=resolved_model,
        )

    raise ValueError(
        f"Unknown LLM_PROVIDER '{provider}'. Supported: anthropic"
    )
