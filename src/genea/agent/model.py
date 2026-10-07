"""Choose the chat model from settings: local Ollama by default, any PydanticAI model string otherwise."""

from __future__ import annotations

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.ollama import OllamaProvider

from genea.config import Settings


def is_local(settings: Settings) -> bool:
    return settings.model.startswith("ollama:")


def build_model(settings: Settings):
    provider, _, name = settings.model.partition(":")
    if provider == "ollama":
        if not name:
            raise ValueError("model must look like 'ollama:<model name>', e.g. 'ollama:qwen3:14b'")
        return OpenAIChatModel(name, provider=OllamaProvider(base_url=settings.ollama_url.rstrip("/") + "/v1"))
    # e.g. "anthropic:claude-sonnet-5-5" (needs `uv sync --extra cloud` and ANTHROPIC_API_KEY)
    return settings.model
