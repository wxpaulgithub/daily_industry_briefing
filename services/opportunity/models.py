import hashlib
import re
import time
from dataclasses import asdict, dataclass, field


PROJECT_TYPES = {
    "NEW_BUILD", "RETROFIT", "MAINTENANCE", "SOFTWARE",
    "EQUIPMENT", "MIXED", "UNKNOWN",
}
PROJECT_STAGES = {"EARLY_SIGNAL", "PROCUREMENT", "AWARD", "CLOSED", "UNKNOWN"}


def make_project_key(title: str, owner: str = "", city: str = "") -> str:
    """Build a stable key without relying on a tracking URL."""
    raw = f"{title}|{owner}|{city}".lower()
    raw = re.sub(r"(?:中标候选人|重新招标|变更公告|二次|澄清|招标|采购|中标|成交|公告|公示|项目|工程)", "", raw)
    raw = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", raw)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True)
class SearchSource:
    url: str
    title: str = ""
    snippet: str = ""
    origin: str = ""
    retrieved_at: str = ""


@dataclass
class OpportunityCandidate:
    title: str
    url: str
    summary: str = ""
    source_name: str = ""
    published: str = ""
    published_ts: float = 0.0
    content: str = ""
    discovery_method: str = "existing"
    verified_url: str = ""
    snippet_origin: str = ""
    search_sources: list[dict] = field(default_factory=list)
    content_hash: str = ""
    discovery_channels: list[str] = field(default_factory=list)
    triage_score: float = 0.0
    triage_reason: str = ""
    freshness_flags: list[str] = field(default_factory=list)


@dataclass
class ProjectOpportunity:
    title: str
    source_url: str
    source_name: str = ""
    discovery_url: str = ""
    official_source_url: str = ""
    owner: str = ""
    province: str = ""
    city: str = ""
    published: str = ""
    published_ts: float = 0.0
    deadline: str = ""
    budget: str = ""
    budget_amount: float | None = None
    summary: str = ""
    project_type: str = "UNKNOWN"
    stage: str = "UNKNOWN"
    relevance_score: float = 0.0
    urgency_score: float = 0.0
    freshness_score: float = 0.0
    company_fit_score: float = 0.0
    source_score: float = 0.0
    final_score: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    technical_scope: list[str] = field(default_factory=list)
    priority: str = "WATCH"
    opportunity_reason: str = ""
    entry_point: str = ""
    evidence: list[dict] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    source_tier: int = 3
    research_mode: str = "rules"
    provider: str = ""
    verification_status: str = ""
    source_content_hash: str = ""
    notice_version: str = ""
    last_researched_at: float = 0.0
    is_new: bool = False
    is_updated: bool = False
    first_seen_at: float = field(default_factory=time.time)
    last_seen_at: float = field(default_factory=time.time)
    last_reported_at: float = 0.0
    last_reported_stage: str = ""
    last_digest_hash: str = ""
    previous_stage: str = ""
    previous_budget: str = ""
    previous_deadline: str = ""
    project_key: str = ""

    def __post_init__(self) -> None:
        if self.project_type not in PROJECT_TYPES:
            self.project_type = "UNKNOWN"
        if self.stage not in PROJECT_STAGES:
            self.stage = "UNKNOWN"
        if not self.project_key:
            self.project_key = make_project_key(self.title, self.owner, self.city)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectOpportunity":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in allowed})


def is_unverified(item) -> bool:
    """Legacy snapshots retain conservative excerpt provenance across failures."""
    return (item.verification_status in {"preview", "unverified"}
            or item.research_mode == "preview"
            or any(flag in item.risk_flags for flag in (
                "search_excerpt_only", "search_excerpt_supports_some_fields", "original_page_unavailable")))


class OpportunityBatch(list):
    """Public list of active results plus observations retained outside the digest."""
    def __init__(self, items=(), observations=None):
        super().__init__(items)
        self.observations = list(self if observations is None else observations)
