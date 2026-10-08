"""Publication clues and conservative historical-title protection."""
import re
from html.parser import HTMLParser
from html import unescape
from .facts import parse_date, deadline_expired
from .runtime import local_now

VERSION_TERMS = ("二次", "重新招标", "变更", "澄清", "延期", "重启")

class _PublicationMetadata(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values = []
    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        key = (values.get("property") or values.get("name") or values.get("itemprop") or "").lower()
        if tag == "meta" and key in {"article:published_time", "datepublished", "pubdate", "publishdate", "publication_date"}:
            self.values.append((values.get("content", ""), self.get_starttag_text()))
        if tag == "time" and (key == "datepublished" or "publish" in values.get("class", "").lower()):
            self.values.append((values.get("datetime", ""), self.get_starttag_text()))

def html_publication(html):
    parser = _PublicationMetadata()
    parser.feed(html)
    # Preserve the exact source fragment so any resulting factual evidence
    # still has to quote the original HTML, not an invented publication label.
    values = list(parser.values)
    for block in re.findall(r'<script[^>]+type=[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>', html, re.I | re.S):
        for match in re.finditer(r'"datePublished"\s*:\s*"([^"<>]+)"', block):
            values.append((match.group(1), match.group(0)))
    parsed = [(parse_date(unescape(value)), quote) for value, quote in values]
    parsed = [(date, quote) for date, quote in parsed if date]
    if len({date for date, _ in parsed}) == 1:
        date, quote = parsed[0]
        return date.isoformat(), quote
    return "", ""

def body_publication(text):
    match = re.search(r'(?:发布时间|发布日期|发布于|发表于)\s*[：:]?\s*(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?(?:[ T\s]+\d{1,2}[:：]\d{2}(?:[:：]\d{2})?)?)', text)
    return match.group(1) if match and parse_date(match.group(1)) else ""

def stale_title_risk(title, published_ts=0, deadline="", now=None):
    now = now or local_now()
    years = [int(x) for x in re.findall(r'(?<!\d)(20\d{2})年?(?!\d)', title)]
    if not years or max(years) >= now.year:
        return False
    if published_ts and -86400 <= now.timestamp() - published_ts <= 90 * 86400:
        return False
    if deadline and parse_date(deadline) and not deadline_expired(deadline, now):
        return False
    return True

def candidate_title_risk(candidate):
    from .enrichment import extract_deadline
    return stale_title_risk(candidate.title, candidate.published_ts,
                            extract_deadline(candidate.summary + " " + candidate.content))
