"""Bounded HTML metadata extraction; never substitute crawl time for publication."""
from datetime import datetime
from html import unescape
import json
import asyncio
import ipaddress
import socket
import re
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from lxml import html as lhtml
import trafilatura

DATE = re.compile(r"(?<!\d)(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})(?:日)?(?:[T ](\d{1,2}):(\d{2})(?::(\d{2}))?(Z|[+-]\d{2}:?\d{2})?)?")
REVERSE_DATE = re.compile(r"(?<!\d)(\d{2})-(\d{2})\s+(20\d{2})(?!\d)")


def clean_text(text):
    return " ".join(unescape(text or "").split())


def parse_date(text):
    match = DATE.search(text or "")
    if not match:
        reverse = REVERSE_DATE.search(text or "")
        if not reverse:
            return 0.0, ""
        return parse_date(f"{reverse[3]}-{reverse[1]}-{reverse[2]}")
    year, month, day, hour, minute, second, offset = match.groups()
    try:
        stamp = f"{int(year):04}-{int(month):02}-{int(day):02}T{int(hour or 0):02}:{int(minute or 0):02}:{int(second or 0):02}" + (offset.replace("Z", "+00:00") if offset else "")
        value = datetime.fromisoformat(stamp)
        if not value.tzinfo:
            value = value.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        return value.timestamp(), value.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    except ValueError:
        return 0.0, ""


def unique_date(values):
    dates = [parse_date(v) for v in values]
    dates = [(ts, label) for ts, label in dates if ts]
    if len({label for _, label in dates}) == 1:
        return dates[0]
    return 0.0, ""


def primary_metadata(text, url):
    root = lhtml.fromstring(text)
    title_values = root.xpath('//meta[@property="og:title"]/@content') or root.xpath('//h1//text()')
    title = clean_text(" ".join(title_values))
    values = root.xpath('//meta[@property="article:published_time" or @itemprop="datePublished" or @name="datePublished" or @name="publishdate" or @name="pubdate" or @name="PubDate"]/@content')
    for script in root.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            data = json.loads(script)
            objects = data if isinstance(data, list) else [data]
            for item in objects:
                if isinstance(item, dict):
                    nodes = item.get("@graph", [item])
                    for node in nodes:
                        if isinstance(node, dict) and node.get("datePublished"):
                            values.append(str(node["datePublished"]))
        except (ValueError, TypeError):
            pass
    if not values:
        # Exact date nodes only. Related-article links and footer text do not establish publication.
        values = root.xpath('//time[not(ancestor::a)]/@datetime')
        for node in root.xpath('//*[contains(translate(@class,"TIMEPUBDATE","timepubdate"),"time") or contains(translate(@class,"DATE","date"),"date") or contains(@class,"publish")]'):
            if node.tag in ("script", "style") or any(a.tag == "a" for a in node.iterancestors()):
                continue
            value = clean_text(node.text_content())
            if len(value) < 80 and DATE.search(value):
                values.append(value)
    ts, published = unique_date(values)
    summary = clean_text(trafilatura.extract(text, include_comments=False, include_tables=False) or "")
    if title and summary.startswith(title):
        summary = summary[len(title):].strip()
    if not summary:
        summary = clean_text(" ".join(root.xpath('//meta[@name="description" or @property="og:description"]/@content')))
    images = root.xpath('//meta[@property="og:image"]/@content')
    return {"title": title, "summary": summary[:200], "published_ts": ts, "published": published, "image_url": urljoin(url, images[0]) if images else ""}


class SourceReadError(ValueError):
    """Stable error codes safe to record in source diagnostics."""


async def public_news_url(url, check_dns=True):
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in ("http", "https") or not host or parsed.username or parsed.password:
            return False
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan")):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            if "." not in host:
                return False
        if not check_dns:
            return True
        rows = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM), timeout=3)
        return bool(rows) and all(ipaddress.ip_address(row[4][0]).is_global for row in rows)
    except (OSError, ValueError, asyncio.TimeoutError):
        return False


async def read_public_text(client, url, *, timeout=10, size_limit=2*1024*1024, check_dns=True):
    for _ in range(5):
        if not await public_news_url(url, check_dns):
            raise SourceReadError("source_not_public")
        async with client.stream("GET", url, follow_redirects=False, headers={"Accept":"application/rss+xml, application/xml, text/xml, text/html, application/json", "Referer":url}, timeout=timeout) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise SourceReadError("invalid_redirect")
                url = urljoin(str(response.url), location)
                continue
            response.raise_for_status()
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > size_limit:
                    raise SourceReadError("response_size_limit")
            return bytes(content).decode(response.encoding or "utf-8", errors="replace"), str(response.url)
    raise SourceReadError("redirect_limit")
