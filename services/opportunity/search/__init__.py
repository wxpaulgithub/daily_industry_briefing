from .base import SearchProvider
from .existing_search import ExistingSearchProvider
from .provider_native_search import NativeSearchProvider
from .openai_web_search import OpenAIWebSearch
from .glm_web_search import GLMWebSearch
from ..ai_client import create_llm_provider


def create_search_provider(settings, usage, llm, queries):
    choice = settings.search_provider
    if choice == "existing":
        return ExistingSearchProvider(llm, queries, usage)
    if choice == "glm":
        return GLMWebSearch(settings, usage)
    if choice == "auto" and llm.name == "glm" and settings.glm_web_search_enabled:
        return GLMWebSearch(settings, usage)
    provider = llm if choice in {"auto", "native"} else create_llm_provider(settings, usage, choice)
    if provider.capabilities.native_web_search:
        native_class = OpenAIWebSearch if provider.name == "openai" else NativeSearchProvider
        return native_class(provider)
    if choice == "native" and provider.name == "glm" and settings.glm_web_search_enabled:
        return GLMWebSearch(settings, usage)
    return ExistingSearchProvider(llm, queries, usage)


__all__ = ["SearchProvider", "create_search_provider"]
