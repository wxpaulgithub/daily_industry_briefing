"""Stable identity, observed changes and deduplication against last sent facts."""
import hashlib
import json
import re
import time

from .facts import normalize_url, content_signature
from .models import ProjectOpportunity, is_unverified
from .runtime import read_json, write_json


def title_identity(title: str) -> str:
    text = re.sub(r"(?:中标候选人|重新招标|变更公告|二次|澄清|招标|采购|中标|成交|公告|公示|项目|工程)", "", title.lower())
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", text)


def material_hash(item) -> str:
    values = {key: getattr(item, key) for key in ("stage", "budget", "deadline")}
    values["technical_scope"] = sorted(set(item.technical_scope))
    if item.notice_version:
        values["notice_version"] = item.notice_version
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]


def notice_version(title, published, url):
    if not re.search(r"二次|重新招标|澄清|变更|重新启动|重启", title):
        return ""
    return content_signature(f"{title}|{published}|{normalize_url(url)}")[:24]


def recent_memory(memory, now_ts, days=14, limit=50):
    keys = ("project_key", "title", "owner", "stage", "budget", "deadline", "technical_scope",
            "source_url", "source_content_hash", "notice_version", "last_researched_at", "verification_status", "last_seen_at")
    rows = sorted(memory.values(), key=lambda row: row.get("last_seen_at", 0), reverse=True)
    return [{key: row.get(key, "") for key in keys} for row in rows
            if row.get("last_seen_at", 0) >= now_ts - days * 86400][:limit]


def split_reusable(candidates, memory, now_ts, recheck_hours=24):
    pending, reused = [], []
    for candidate in candidates:
        probe = ProjectOpportunity(candidate.title, candidate.url)
        old = find_previous(probe, memory)
        reusable = (old and candidate.content_hash and candidate.verified_url
                    and old.get("source_content_hash") == candidate.content_hash
                    and old.get("research_mode") == "ai"
                    and old.get("verification_status") == "verified"
                    and 0 <= now_ts - old.get("last_researched_at", 0) < recheck_hours * 3600
                    and notice_version(candidate.title, candidate.published, candidate.url) == old.get("notice_version", ""))
        if reusable:
            item = ProjectOpportunity.from_dict(old)
            if not is_unverified(item):
                reused.append(item)
                continue
        pending.append(candidate)
    return pending, reused


def load_memory(base_dir):
    return read_json(base_dir / "opportunities" / "index.json")


def find_previous(item, memory):
    if item.project_key in memory:
        return memory[item.project_key]
    urls = {normalize_url(url) for url in [item.source_url, item.discovery_url, *item.source_urls]} - {""}
    for row in memory.values():
        old_urls = {normalize_url(url) for url in [row.get("source_url", ""), row.get("discovery_url", ""), *row.get("source_urls", [])]} - {""}
        same_url = bool(urls & old_urls)
        same_title = title_identity(item.title) == title_identity(row.get("title", ""))
        conflicts = any(getattr(item, field) and row.get(field) and getattr(item, field) != row[field]
                        for field in ("owner", "city"))
        if (same_url or same_title) and not conflicts:
            return row
    return None


def apply_history(items, memory, now_ts: float | None = None):
    from .models import ProjectOpportunity
    now_ts = now_ts or time.time()
    for item in items:
        old = find_previous(item, memory)
        item.is_new = old is None
        item.is_updated = False
        if old:
            item.project_key = old.get("project_key") or item.project_key
            item.first_seen_at = float(old.get("first_seen_at") or item.first_seen_at)
            item.previous_stage = old.get("stage", "")
            item.previous_budget = old.get("budget", "")
            item.previous_deadline = old.get("deadline", "")
            if item.published_ts and old.get("published_ts", 0) > item.published_ts:
                for field in ("stage", "budget", "deadline", "technical_scope", "notice_version", "source_content_hash"):
                    setattr(item, field, old.get(field, getattr(item, field)))
            if old.get("stage") in {"AWARD", "CLOSED"} and old.get("published_ts", 0) >= item.published_ts:
                # A stale procurement page cannot reopen a later closed/awarded project.
                item.stage = old["stage"]
                item.published = old.get("published", item.published)
                item.published_ts = old.get("published_ts", item.published_ts)
            for field in ("last_reported_at", "last_reported_stage", "last_digest_hash"):
                setattr(item, field, old.get(field) or getattr(item, field))
            item.is_updated = material_hash(item) != material_hash(ProjectOpportunity.from_dict(old))
        item.last_seen_at = now_ts
    return items


def needs_report(item) -> bool:
    return item.stage not in {"AWARD", "CLOSED"} and item.priority != "DROP" and (
        not item.last_reported_at or item.last_digest_hash != material_hash(item)
    )


def mark_reported(items, date_str: str, base_dir, now_ts: float | None = None):
    """Called only after WeCom acknowledges delivery; update memory and saved rows."""
    now_ts = now_ts or time.time()
    memory = load_memory(base_dir)
    marks = {}
    for item in items:
        marks[item.project_key] = {"last_reported_at": now_ts, "last_reported_stage": item.stage,
                                   "last_digest_hash": material_hash(item)}
        if item.project_key in memory:
            memory[item.project_key].update(marks[item.project_key])
    write_json(base_dir / "opportunities" / "index.json", memory)
    path = base_dir / "opportunities" / f"{date_str}.json"
    rows = read_json(path, [])
    for row in rows:
        if row.get("project_key") in marks:
            row.update(marks[row["project_key"]])
    write_json(path, rows)
