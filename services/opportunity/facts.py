"""Deterministic date, amount and URL handling for AI supplied facts."""
import ipaddress
import hashlib
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SHANGHAI = timezone(timedelta(hours=8))


def normalize_url(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            return ""
        host = parts.hostname.lower().rstrip(".")
        if host in {"localhost", "localhost.localdomain"} or "." not in host:
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            if host.endswith((".local", ".internal", ".localhost", ".test", ".invalid")):
                return ""
        if parts.port and parts.port not in {80, 443}:
            return ""
        query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                 if not key.lower().startswith("utm_") and key.lower() not in {"spm", "from", "fbclid"}]
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", urlencode(query), ""))
    except (ValueError, AttributeError):
        return ""


def parse_date(value: str) -> datetime | None:
    match = re.search(r"(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})日?(?:[ T\s]+(\d{1,2})[:：](\d{2})(?:[:：](\d{2}))?)?", value or "")
    if not match:
        return None
    year, month, day, hour, minute, second = match.groups()
    try:
        return datetime(int(year), int(month), int(day), int(hour or 0), int(minute or 0), int(second or 0), tzinfo=SHANGHAI)
    except ValueError:
        return None


def deadline_expired(value: str, now: datetime) -> bool:
    deadline = parse_date(value)
    if not deadline:
        return False
    if not re.search(r"\d{1,2}[:：]\d{2}", value):
        deadline += timedelta(days=1)  # Unknown time: keep through the stated day.
    return deadline <= now


def budget_amount(value: str) -> float | None:
    match = re.search(r"([0-9]+(?:[,.][0-9]+)*)\s*(亿元|万元|万|元)", value or "")
    if not match:
        return None
    try:
        number = float(match.group(1).replace(",", ""))
        return number * {"亿元": 1e8, "万元": 1e4, "万": 1e4, "元": 1}[match.group(2)]
    except ValueError:
        return None


def content_signature(value: str) -> str:
    return hashlib.sha256(compact(value).encode("utf-8")).hexdigest() if value else ""


def compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()
