from typing import Optional
from app.core.config import settings
from app.services.ai.base import BaseAIProvider
from app.services.ai.mock_provider import MockProvider
from app.services.ai.openai_provider import OpenAIProvider
from app.services.ai.gemini_provider import GeminiProvider


_cached_provider: Optional[BaseAIProvider] = None


def get_ai_provider(
    provider_name: Optional[str] = None,
    settings_override=None,
) -> BaseAIProvider:
    """
    Factory function to retrieve the configured AI provider.
    Supports 'gemini', 'openai', and 'mock' dynamically without code changes.
    """
    global _cached_provider

    cfg = settings_override or settings
    name = (provider_name or cfg.AI_PROVIDER).lower().strip()

    if name == "mock":
        return MockProvider()
    elif name == "gemini":
        return GeminiProvider(
            api_key=cfg.resolved_api_key,
            default_model=cfg.resolved_model,
            base_url=cfg.GEMINI_BASE_URL,
            default_timeout=cfg.AI_TIMEOUT,
            default_max_tokens=cfg.AI_MAX_TOKENS,
            default_temperature=cfg.AI_TEMPERATURE,
        )
    elif name == "openai":
        return OpenAIProvider(
            api_key=cfg.resolved_api_key,
            default_model=cfg.resolved_model,
            base_url=cfg.AI_BASE_URL,
            default_timeout=cfg.AI_TIMEOUT,
            default_max_tokens=cfg.AI_MAX_TOKENS,
            default_temperature=cfg.AI_TEMPERATURE,
        )
    elif name in ("anthropic", "azure"):
        # Reserved for future phases; fallback to MockProvider with clear warning
        return MockProvider()
    else:
        raise ValueError(f"Unsupported AI provider: '{name}'. Supported: 'gemini', 'openai', 'mock'.")
