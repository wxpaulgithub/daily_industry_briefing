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
    raw = re.sub(r"(?:招标|采购|中标|成交|公告|公示|项目|工程)", "", raw)
    raw = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", raw)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


@dataclass
class OpportunityCandidate:
    title: str
    url: str
    summary: str = ""
    source_name: str = ""
    published: str = ""
    published_ts: float = 0.0
    content: str = ""


@dataclass
class ProjectOpportunity:
    title: str
    source_url: str
    source_name: str = ""
    discovery_url: str = ""
    owner: str = ""
    province: str = ""
    city: str = ""
    published: str = ""
    published_ts: float = 0.0
    deadline: str = ""
    budget: str = ""
    summary: str = ""
    project_type: str = "UNKNOWN"
    stage: str = "UNKNOWN"
    relevance_score: float = 0.0
    urgency_score: float = 0.0
    source_score: float = 0.0
    final_score: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    first_seen_at: float = field(default_factory=time.time)
    last_seen_at: float = field(default_factory=time.time)
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
