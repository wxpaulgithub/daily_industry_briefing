"""WeCom only consumes durable snapshots; acknowledgement controls send memory."""
import asyncio
import hashlib
import logging
import re
import time

import httpx

from .facts import deadline_expired
from .models import is_unverified
from .project_memory import mark_reported, material_hash, needs_report
from .runtime import local_now, read_json, write_json
from .settings import OpportunitySettings
from .storage import load_snapshot

logger = logging.getLogger(__name__)
_publish_lock = asyncio.Lock()
STAGE_LABELS = {"EARLY_SIGNAL": "采购前期", "PROCUREMENT": "采购/招标", "AWARD": "已中标", "CLOSED": "已结束", "UNKNOWN": "阶段待核实"}
PRIORITY_LABELS = {"A": "重点关注", "B": "值得跟进", "WATCH": "观察", "DROP": "低优先度"}


def _text(value: str, length: int) -> str:
    value = re.sub(r"[\r\n]+", " ", value or "")
    value = re.sub(r"[\\`*_\[\]()<>#]", "", value)
    return value[:length] + ("…" if len(value) > length else "")


def build_wecom_digest(items, date_str: str, site_url: str, *, limit: int = 5) -> str:
    items = list(items[:min(limit, 5)])
    header = f"📌 智能仓储商机日报｜{date_str}\n\n今日筛选 {len(items)} 条\n"
    header += f"A级 {sum(item.priority == 'A' for item in items)} 条 · B级 {sum(item.priority == 'B' for item in items)} 条 · 观察 {sum(item.priority == 'WATCH' for item in items)} 条\n"
    if any(item.research_mode == "fallback" for item in items):
        header += "\n部分项目为规则筛选结果，请核对原文。\n"
    if any(item.research_mode == "preview" for item in items):
        header += "\n部分项目仅依据搜索摘要初步判断，原文尚未读取，不进入自动推送。\n"
    footer = f"\n👉 [查看完整日报与原始来源]({site_url.rstrip('/')}/?scope=opportunity)"
    blocks = []
    for number, item in enumerate(items, 1):
        symbol = {"A": "🔴", "B": "🟠"}.get(item.priority, "⚪")
        change = " · 项目更新" if item.last_reported_at and item.last_digest_hash != material_hash(item) else ""
        fallback = " · 搜索摘要预览" if item.research_mode == "preview" else " · 规则筛选" if item.research_mode == "fallback" else ""
        blocks.append(
            f"\n{symbol} **{number}. {_text(item.title, 54)}**{change}{fallback}\n"
            f"{_text(item.province or item.city or '地区待核实', 12)} · {_text(item.budget or '预算待核实', 28)} · {STAGE_LABELS.get(item.stage, '阶段待核实')}\n"
            + (f"涉及：{_text(' / '.join(item.technical_scope), 45)}\n" if item.technical_scope else "")
            + (f"切入：{_text(item.entry_point, 60)}\n" if item.entry_point else "")
        )
    message = header + "".join(blocks) + footer
    if len(message.encode("utf-8")) > 4096:
        # Keep every selected title and preserve the page entry under WeCom's limit.
        available = max(4096 - len((header + footer).encode("utf-8")) - 32, 0)
        budget = available // max(len(blocks), 1)
        blocks = [block.encode("utf-8")[:budget].decode("utf-8", errors="ignore") + "…\n" for block in blocks]
        message = header + "".join(blocks) + footer
    if len(message.encode("utf-8")) > 4096:
        raise ValueError("digest_exceeds_wecom_limit")
    return message


async def _send(message, settings, client=None):
    if not settings.webhook_url:
        return {"ok": False, "reason": "webhook_not_configured"}
    payload = {"msgtype": "markdown", "markdown": {"content": message}}
    try:
        if client:
            response = await client.post(settings.webhook_url, json=payload)
        else:
            async with httpx.AsyncClient(timeout=20) as http_client:
                response = await http_client.post(settings.webhook_url, json=payload)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            return {"ok": False, "reason": "wecom_invalid_response"}
        if data.get("errcode") != 0:
            return {"ok": False, "reason": "wecom_rejected", "errcode": data.get("errcode") if isinstance(data.get("errcode"), int) else None}
        return {"ok": True}
    except (httpx.HTTPError, ValueError):
        return {"ok": False, "reason": "wecom_delivery_failed"}


async def publish_test(settings=None, client=None):
    settings = settings or OpportunitySettings.from_env()
    result = await _send("智能仓储商机日报\n\n推送通道测试成功。", settings, client)
    path = settings.runtime_dir / "opportunity_publish_status.json"
    status = read_json(path)
    status.update(last_test_status="success" if result["ok"] else "failed", last_test_at=local_now().isoformat())
    write_json(path, status)
    return {"status": status["last_test_status"], **result}


async def publish_today(settings=None, *, date_str=None, client=None, retry=False):
    settings = settings or OpportunitySettings.from_env()
    date_str = date_str or local_now().date().isoformat()
    async with _publish_lock:
        path = settings.runtime_dir / "opportunity_publish_status.json"
        status = read_json(path)
        if not settings.webhook_url:
            return {"status": "not_configured"}
        items = load_snapshot(date_str, settings.output_dir)
        receipts = status.get("delivered_projects", {})
        selected = [item for item in items if not is_unverified(item) and needs_report(item)
                    and receipts.get(item.project_key) != material_hash(item)
                    and not deadline_expired(item.deadline, local_now())][:settings.digest_limit]
        if not selected:
            return {"status": "no_new_opportunities", "count": 0}
        message = build_wecom_digest(selected, date_str, settings.site_url, limit=settings.digest_limit)
        digest = hashlib.sha256(message.encode()).hexdigest()
        identity = {item.project_key: material_hash(item) for item in selected}
        same_pending = status.get("date") == date_str and status.get("digest_hash") == digest
        if retry and (not same_pending or status.get("pending_projects") != identity):
            status.update(status="superseded", next_retry_at=0)
            write_json(path, status)
            return {"status": "superseded"}
        attempts = int(status.get("attempts") or 0) if same_pending else 0
        if attempts >= settings.publish_attempts:
            return {"status": "retry_exhausted"}
        # Persist intent before the request, so failures/restarts retain bounded attempts.
        status.update(date=date_str, status="sending", digest_hash=digest, pending_projects=identity,
                      attempts=attempts + 1, count=len(selected), next_retry_at=0, last_attempt_at=local_now().isoformat())
        write_json(path, status)
        result = await _send(message, settings, client)
        if result["ok"]:
            receipts.update(identity)
            status.update(status="success", delivered_projects=receipts, last_success=local_now().isoformat(), next_retry_at=0, reason="")
            write_json(path, status)
            try:
                mark_reported(selected, date_str, settings.output_dir)
            except OSError:
                # The durable delivery receipt prevents a second send even if memory fails.
                status["memory_update_failed"] = True
                write_json(path, status)
        else:
            status.update(status="failed", reason=result["reason"],
                          next_retry_at=time.time() + settings.publish_retry_seconds if attempts + 1 < settings.publish_attempts else 0)
            write_json(path, status)
        logger.info("[OpportunityPublisher] status=%s count=%d attempts=%d", status["status"], len(selected), status["attempts"])
        return {"status": status["status"], "count": len(selected), "attempts": status["attempts"], **result}


async def retry_pending(settings=None, client=None):
    settings = settings or OpportunitySettings.from_env()
    if not settings.enabled:
        return {"status": "disabled"}
    status = read_json(settings.runtime_dir / "opportunity_publish_status.json")
    if status.get("status") != "failed" or not status.get("next_retry_at") or status["next_retry_at"] > time.time():
        return {"status": "idle"}
    if status.get("date") != local_now().date().isoformat():
        status.update(status="expired", next_retry_at=0)
        write_json(settings.runtime_dir / "opportunity_publish_status.json", status)
        return {"status": "expired"}
    return await publish_today(settings, date_str=status["date"], client=client, retry=True)
