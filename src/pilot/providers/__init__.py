"""Adapters keep external wire formats out of the agent loop."""

import httpx

from pilot.config import Config
from pilot.providers.base import LLMProvider
from pilot.providers.gemini import GeminiProvider
from pilot.providers.ollama import OllamaProvider


def create_provider(config: Config, client: httpx.Client) -> LLMProvider:
    if config.provider == "gemini":
        return GeminiProvider(
            client, config.gemini, config.gemini_api_key.get_secret_value(), config.retry
        )
    return OllamaProvider(client, config.ollama, config.retry)
