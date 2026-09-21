import json
import time
from datetime import datetime
from pathlib import Path

from config import OUTPUT_DIR
from .models import ProjectOpportunity


def opportunity_dir(base_dir: Path = OUTPUT_DIR) -> Path:
    path = base_dir / "opportunities"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_snapshot(date_str: str | None = None, base_dir: Path = OUTPUT_DIR) -> list[ProjectOpportunity]:
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
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
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    now = time.time()
    index = _load_index(base_dir)
    deduped: dict[str, ProjectOpportunity] = {}
    for item in sorted(items, key=lambda x: x.final_score, reverse=True):
        old = index.get(item.project_key)
        if old:
            item.first_seen_at = float(old.get("first_seen_at") or item.first_seen_at)
        item.last_seen_at = now
        current = deduped.get(item.project_key)
        if current is None or item.final_score > current.final_score:
            deduped[item.project_key] = item
    rows = [x.to_dict() for x in deduped.values()]
    out_dir = opportunity_dir(base_dir)
    snapshot = out_dir / f"{date_str}.json"
    snapshot.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    for row in rows:
        index[row["project_key"]] = row
    (out_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return snapshot
