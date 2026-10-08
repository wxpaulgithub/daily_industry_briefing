"""Atomic runtime JSON and bounded, auditable API usage."""
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


class SecretURLFilter(logging.Filter):
    def filter(self, record):
        def redact(value):
            return re.sub(r"(?i)([?&](?:key|token|access_token|api_key)=)[^&\s\"']+", r"\1[REDACTED]", str(value))
        record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(value) if any(token in str(value).lower() for token in ("key=", "token=")) else value for value in record.args)
        return True


logging.getLogger("httpx").addFilter(SecretURLFilter())


def local_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {} if default is None else default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.stem + "-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def update_status(settings, stage: str, **values):
    path = settings.runtime_dir / "opportunity_ai_status.json"
    status = read_json(path)
    status.update(stage=stage, updated_at=local_now().isoformat(), **values)
    write_json(path, status)
    return status


def get_status(settings):
    result = {"stage": "IDLE", "last_run": "", "last_success": "", "last_count": 0,
              "last_ai_candidates": 0, "last_web_search_calls": 0,
              **read_json(settings.runtime_dir / "opportunity_ai_status.json")}
    result.update(enabled=settings.enabled, ai_enabled=settings.ai_enabled,
                  ai_configured=settings.configured(), auto_publish=settings.auto_publish,
                  provider=settings.provider, webhook_configured=bool(settings.webhook_url))
    result["publish"] = read_json(settings.runtime_dir / "opportunity_publish_status.json")
    result["last_publish_status"] = result["publish"].get("status", result.get("last_publish_status", "not_configured"))
    return result


class UsageTracker:
    def __init__(self, settings):
        self.settings = settings
        self.calls = 0
        self.search_calls = 0
        self.reserved_search_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.by_provider = {}
        self.search_phases = {phase: {"reserved": 0, "calls": 0, "requests": 0, "unknown_requests": 0}
                              for phase in ("discovery", "verification")}

    def remaining(self, phase="discovery") -> int:
        total = self.settings.total_search_budget
        if total is None:
            total = self.settings.max_tool_calls
        limit = (self.settings.discovery_search_budget if phase == "discovery"
                 else self.settings.verification_search_budget)
        return max(0, min(limit - self.search_phases[phase]["reserved"], total - self.reserved_search_calls))

    def reserve(self, search_budget: int = 0, *, phase="discovery", llm=True) -> int:
        if llm and self.calls >= self.settings.max_llm_calls:
            raise RuntimeError("llm_call_budget_exhausted")
        allowance = min(search_budget, self.remaining(phase))
        if search_budget and not allowance:
            raise RuntimeError("search_call_budget_exhausted")
        if llm:
            self.calls += 1
        self.reserved_search_calls += allowance
        if search_budget:
            self.search_phases[phase]["reserved"] += allowance
            self.search_phases[phase]["requests"] += 1
        return allowance

    def settle_search(self, phase, reserved, actual=None):
        if not reserved:
            return
        row = self.search_phases[phase]
        if actual is None:
            row["unknown_requests"] += 1  # Unknown remote consumption stays charged.
            return
        row["calls"] += actual
        released = max(reserved - actual, 0)
        row["reserved"] -= released
        self.reserved_search_calls -= released

    def search_summary(self):
        return {"discovery_search_calls": self.search_phases["discovery"]["calls"],
                "verification_search_calls": self.search_phases["verification"]["calls"],
                "total_search_calls": sum(row["calls"] for row in self.search_phases.values()),
                "search_budget_charged": self.reserved_search_calls,
                "search_phases": self.search_phases}

    def record(self, provider: str, input_tokens: int, output_tokens: int, search_calls: int = 0):
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.search_calls += search_calls
        row = self.by_provider.setdefault(provider, {"responses": 0, "input_tokens": 0, "output_tokens": 0, "web_search_calls": 0})
        for key, count in (("responses", 1), ("input_tokens", input_tokens), ("output_tokens", output_tokens), ("web_search_calls", search_calls)):
            row[key] += count

    def save(self):
        path = self.settings.runtime_dir / "opportunity_usage.json"
        history = read_json(path)
        date = local_now().date().isoformat()
        previous = history.get(date, {})
        row = {"date": date, "responses": self.calls, "web_search_calls": self.search_calls,
               "input_tokens": self.input_tokens, "output_tokens": self.output_tokens, **self.search_summary()}
        for key in ("responses", "web_search_calls", "input_tokens", "output_tokens"):
            row[key] += previous.get(key, 0)
        for key in ("discovery_search_calls", "verification_search_calls", "total_search_calls", "search_budget_charged"):
            row[key] += previous.get(key, 0)
        row["search_phases"] = {phase: {key: value + previous.get("search_phases", {}).get(phase, {}).get(key, 0)
                                      for key, value in values.items()}
                                for phase, values in self.search_phases.items()}
        rates = (self.settings.input_cost_per_million, self.settings.output_cost_per_million,
                 self.settings.search_cost_per_thousand)
        providers = previous.get("providers", {})
        for name, values in self.by_provider.items():
            old = providers.setdefault(name, {})
            for key, count in values.items():
                old[key] = old.get(key, 0) + count
        row["providers"] = providers
        row["estimated_cost"] = (round(row["input_tokens"] * rates[0] / 1e6 + row["output_tokens"] * rates[1] / 1e6
                                      + row["web_search_calls"] * rates[2] / 1000, 6)
                                  if all(rate is not None for rate in rates) and len(providers) <= 1 else None)
        history[date] = row
        write_json(path, dict(sorted(history.items())[-90:]))
