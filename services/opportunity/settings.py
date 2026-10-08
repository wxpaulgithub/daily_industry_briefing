"""Environment-only credentials and configurable V2 limits."""
import os
from dataclasses import dataclass, field
from pathlib import Path

from config import OUTPUT_DIR, RUNTIME_DIR, CONFIG_DATA_DIR, SITE_URL


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, minimum: int = 1, maximum: int = 10000) -> int:
    return max(minimum, min(int(os.getenv(name) or default), maximum))


@dataclass(frozen=True)
class OpportunitySettings:
    enabled: bool = True
    ai_enabled: bool = True
    auto_publish: bool = False
    provider: str = "openai"
    fallback_provider: str = ""
    search_provider: str = "auto"
    openai_api_key: str = field(default="", repr=False)
    openai_model: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    glm_api_key: str = field(default="", repr=False)
    glm_model: str = ""
    glm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    glm_web_search_enabled: bool = False
    reasoning: str = "medium"
    research_limit: int = 12
    research_wave_size: int = 6
    research_hard_limit: int = 18
    verified_target: int = 5
    triage_enabled: bool = True
    resolver_max_searches: int = 3
    resolver_min_score: int = 75
    candidate_limit: int = 60
    digest_limit: int = 5
    display_limit: int = 10
    search_concurrency: int = 1
    concurrency: int = 2
    task_timeout: int = 1200
    request_timeout: int = 180
    max_tool_calls: int = 12
    discovery_search_budget: int = 12
    verification_search_budget: int = 24
    total_search_budget: int | None = 36
    memory_days: int = 14
    memory_limit: int = 50
    memory_recheck_hours: int = 24
    max_llm_calls: int = 64
    max_output_tokens: int = 8000
    publish_attempts: int = 3
    publish_retry_seconds: int = 600
    webhook_url: str = field(default="", repr=False)
    site_url: str = SITE_URL
    output_dir: Path = OUTPUT_DIR
    runtime_dir: Path = RUNTIME_DIR
    config_dir: Path = CONFIG_DATA_DIR
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    search_cost_per_thousand: float | None = None

    @classmethod
    def from_env(cls):
        def rate(name):
            value = os.getenv(name)
            return float(value) if value else None
        return cls(
            enabled=env_bool("OPPORTUNITY_ENABLED", True),
            ai_enabled=env_bool("OPPORTUNITY_AI_ENABLED", True),
            auto_publish=env_bool("OPPORTUNITY_AUTO_PUBLISH", False),
            provider=(os.getenv("LLM_PROVIDER") or "openai").strip().lower(),
            fallback_provider=(os.getenv("OPPORTUNITY_FALLBACK_PROVIDER") or "").strip().lower(),
            search_provider=(os.getenv("OPPORTUNITY_SEARCH_PROVIDER") or "auto").strip().lower(),
            openai_api_key=(os.getenv("OPENAI_API_KEY") or "").strip(),
            openai_model=(os.getenv("OPENAI_OPPORTUNITY_MODEL") or "").strip(),
            openai_base_url=(os.getenv("OPENAI_BASE_URL") or cls.openai_base_url).rstrip("/"),
            glm_api_key=(os.getenv("GLM_API_KEY") or "").strip(),
            glm_model=(os.getenv("GLM_OPPORTUNITY_MODEL") or "").strip(),
            glm_base_url=(os.getenv("GLM_BASE_URL") or cls.glm_base_url).rstrip("/"),
            glm_web_search_enabled=env_bool(
                "GLM_WEB_SEARCH_ENABLED", env_bool("GLM_NATIVE_WEB_SEARCH", False)
            ),
            reasoning=os.getenv("OPPORTUNITY_AI_REASONING", os.getenv("OPENAI_OPPORTUNITY_REASONING", "medium")).strip(),
            research_limit=env_int("OPPORTUNITY_AI_RESEARCH_LIMIT", 12, maximum=30),
            research_wave_size=env_int("OPPORTUNITY_RESEARCH_WAVE_SIZE", 6, maximum=10),
            research_hard_limit=env_int("OPPORTUNITY_RESEARCH_HARD_LIMIT", 18, maximum=20),
            verified_target=env_int("OPPORTUNITY_VERIFIED_TARGET", 5, maximum=5),
            triage_enabled=env_bool("OPPORTUNITY_TRIAGE_ENABLED", True),
            resolver_max_searches=env_int("OPPORTUNITY_RESOLVER_MAX_SEARCHES", 3, maximum=4),
            resolver_min_score=env_int("OPPORTUNITY_RESOLVER_MIN_SCORE", 75, maximum=100),
            candidate_limit=env_int("OPPORTUNITY_CANDIDATE_LIMIT", 60, maximum=100),
            digest_limit=env_int("OPPORTUNITY_DIGEST_LIMIT", 5, maximum=5),
            display_limit=env_int("OPPORTUNITY_DISPLAY_LIMIT", 10, maximum=20),
            search_concurrency=env_int("OPPORTUNITY_SEARCH_CONCURRENCY", 1, maximum=4),
            concurrency=env_int("OPPORTUNITY_AI_CONCURRENCY", 2, maximum=4),
            task_timeout=env_int("OPPORTUNITY_TASK_TIMEOUT", 1200, maximum=1800),
            request_timeout=env_int("OPPORTUNITY_AI_REQUEST_TIMEOUT", 180, maximum=600),
            max_tool_calls=env_int("OPENAI_OPPORTUNITY_MAX_TOOL_CALLS", 12, minimum=0, maximum=40),
            discovery_search_budget=env_int("OPPORTUNITY_DISCOVERY_SEARCH_BUDGET", 12, minimum=0, maximum=100),
            verification_search_budget=env_int("OPPORTUNITY_VERIFY_SEARCH_BUDGET", 24, minimum=0, maximum=100),
            total_search_budget=env_int("OPPORTUNITY_TOTAL_SEARCH_BUDGET",
                int(os.getenv("OPENAI_OPPORTUNITY_MAX_TOOL_CALLS") or 36), minimum=0, maximum=200),
            memory_recheck_hours=env_int("OPPORTUNITY_MEMORY_RECHECK_HOURS", 24, maximum=168),
            max_llm_calls=env_int("OPPORTUNITY_MAX_LLM_CALLS", 64, maximum=100),
            max_output_tokens=env_int("OPENAI_OPPORTUNITY_MAX_OUTPUT_TOKENS", 8000, maximum=16000),
            publish_attempts=env_int("OPPORTUNITY_PUBLISH_ATTEMPTS", 3, maximum=3),
            publish_retry_seconds=env_int("OPPORTUNITY_PUBLISH_RETRY_SECONDS", 600, minimum=60),
            webhook_url=(os.getenv("WECOM_OPPORTUNITY_WEBHOOK_URL") or "").strip(),
            site_url=(os.getenv("SITE_URL") or SITE_URL).rstrip("/"),
            runtime_dir=Path(os.getenv("OPPORTUNITY_RUNTIME_DIR") or RUNTIME_DIR),
            input_cost_per_million=rate("OPPORTUNITY_INPUT_COST_PER_MILLION"),
            output_cost_per_million=rate("OPPORTUNITY_OUTPUT_COST_PER_MILLION"),
            search_cost_per_thousand=rate("OPPORTUNITY_SEARCH_COST_PER_THOUSAND"),
        )

    def configured(self, name: str | None = None) -> bool:
        name = name or self.provider
        key, model = {"openai": (self.openai_api_key, self.openai_model),
                      "glm": (self.glm_api_key, self.glm_model)}.get(name, ("", ""))
        return bool(key and model)
