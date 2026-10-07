import json
import time
from pathlib import Path
from datetime import datetime

from config import OUTPUT_DIR
from .models import ProjectOpportunity
from .project_memory import apply_history
from .runtime import local_now, write_json


def opportunity_dir(base_dir: Path = OUTPUT_DIR) -> Path:
    path = base_dir / "opportunities"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_snapshot(date_str: str | None = None, base_dir: Path = OUTPUT_DIR) -> list[ProjectOpportunity]:
    date_str = date_str or local_now().date().isoformat()
    datetime.strptime(date_str, "%Y-%m-%d")
    if len(date_str) != 10:
        raise ValueError("invalid_snapshot_date")
    path = opportunity_dir(base_dir) / f"{date_str}.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [ProjectOpportunity.from_dict(row) for row in data]


def _load_index(base_dir: Path) -> dict[str, dict]:
    path = opportunity_dir(base_dir) / "index.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def save_snapshot(items: list[ProjectOpportunity], date_str: str | None = None, base_dir: Path = OUTPUT_DIR) -> Path:
    date_str = date_str or local_now().date().isoformat()
    datetime.strptime(date_str, "%Y-%m-%d")
    if len(date_str) != 10:
        raise ValueError("invalid_snapshot_date")
    now = time.time()
    index = _load_index(base_dir)
    observed = getattr(items, "observations", items)
    apply_history(observed, index, now)
    deduped: dict[str, ProjectOpportunity] = {}
    for item in items:
        old = index.get(item.project_key)
        if old:
            item.first_seen_at = float(old.get("first_seen_at") or item.first_seen_at)
        item.last_seen_at = now
        current = deduped.get(item.project_key)
        if current is None or item.final_score > current.final_score:
            deduped[item.project_key] = item
    rows = [x.to_dict() for x in deduped.values() if x.stage not in {"AWARD", "CLOSED"}]
    out_dir = opportunity_dir(base_dir)
    snapshot = out_dir / f"{date_str}.json"
    write_json(snapshot, rows)
    stages = {"UNKNOWN": 0, "EARLY_SIGNAL": 1, "PROCUREMENT": 2, "AWARD": 3, "CLOSED": 4}
    for item in sorted(observed, key=lambda item: (item.published_ts, item.source_score, stages[item.stage])):
        index[item.project_key] = item.to_dict()
    for row in rows:
        current = index.get(row["project_key"], {})
        if current.get("stage") not in {"AWARD", "CLOSED"} or row["published_ts"] > current.get("published_ts", 0):
            index[row["project_key"]] = row
    write_json(out_dir / "index.json", index)
    return snapshot
