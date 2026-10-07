"""The only provider factory used by business and search modules."""
from .providers.base import ProviderError
from .providers.openai_provider import OpenAIProvider
from .providers.glm_provider import GLMProvider


def create_llm_provider(settings, usage, name: str | None = None, client=None):
    name = name or settings.provider
    providers = {"openai": OpenAIProvider, "glm": GLMProvider}
    if name not in providers:
        raise ProviderError("unsupported_provider")
    if not settings.configured(name):
        raise ProviderError("provider_not_configured")
    return providers[name](settings, usage, client)
