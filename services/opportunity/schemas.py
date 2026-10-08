"""Strict structured outputs, also validated locally for every provider."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EvidenceItem(StrictModel):
    field: str
    value: str
    source_title: str
    source_url: str
    quote: str = Field(max_length=300)


class AICandidate(StrictModel):
    title: str
    url: str
    summary: str
    source_name: str
    published: str


class CandidateBatch(StrictModel):
    candidates: list[AICandidate]
    expanded_queries: list[str]


class SearchQueries(StrictModel):
    queries: list[str]


class OpportunityResearchResult(StrictModel):
    title: str
    is_real_project: bool
    warehouse_relevance: float = Field(ge=0, le=100)
    owner: str = ""
    province: str = ""
    city: str = ""
    published: str = ""
    budget_text: str = ""
    deadline: str = ""
    stage: Literal["EARLY_SIGNAL", "PROCUREMENT", "AWARD", "CLOSED", "UNKNOWN"]
    project_type: Literal["NEW_BUILD", "RETROFIT", "MAINTENANCE", "SOFTWARE", "EQUIPMENT", "MIXED", "UNKNOWN"]
    technical_scope: list[str] = Field(default_factory=list)
    summary: str = ""
    opportunity_reason: str = ""
    entry_point: str = ""
    source_urls: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)


def strict_schema(model: type[BaseModel]) -> dict:
    """OpenAI requires additionalProperties=false and all properties required."""
    schema = model.model_json_schema()
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(schema)
    return schema


class TriageDecision(StrictModel):
    id: int = Field(ge=1)
    worth_research: bool
    score: float = Field(ge=0, le=100)
    reason: str = Field(max_length=200)


class TriageBatch(StrictModel):
    decisions: list[TriageDecision]
