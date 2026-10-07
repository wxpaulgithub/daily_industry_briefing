"""Offline deployment preflight; emit flags and names, never credentials."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.opportunity.settings import OpportunitySettings


def check_settings(settings):
    errors = []
    if settings.provider not in {"openai", "glm"}:
        errors.append("unsupported_LLM_PROVIDER")
    if settings.fallback_provider not in {"", "openai", "glm"}:
        errors.append("unsupported_OPPORTUNITY_FALLBACK_PROVIDER")
    if settings.search_provider not in {"auto", "native", "existing", "openai", "glm"}:
        errors.append("unsupported_OPPORTUNITY_SEARCH_PROVIDER")
    if settings.auto_publish and settings.enabled and not settings.webhook_url:
        errors.append("auto_publish_requires_WECOM_OPPORTUNITY_WEBHOOK_URL")
    configured = settings.configured() or (settings.fallback_provider and settings.configured(settings.fallback_provider))
    return {
        "ok": not errors, "errors": errors,
        "enabled": settings.enabled, "ai_enabled": settings.ai_enabled,
        "mode": "disabled" if not settings.enabled else "ai" if settings.ai_enabled and configured else "rules_fallback",
        "provider": settings.provider, "search_provider": settings.search_provider,
        "glm_web_search_enabled": settings.glm_web_search_enabled,
        "openai_configured": settings.configured("openai"),
        "glm_configured": settings.configured("glm"), "webhook_configured": bool(settings.webhook_url),
        "auto_publish": settings.auto_publish,
    }


if __name__ == "__main__":
    result = check_settings(OpportunitySettings.from_env())
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result["ok"] else 1)
