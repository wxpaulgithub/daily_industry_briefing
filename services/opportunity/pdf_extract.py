"""Bounded PDF text extraction worker; caller kills it on timeout."""
import io
import json
import sys

from pypdf import PdfReader


def extract(raw):
    reader = PdfReader(io.BytesIO(raw), strict=False)
    pages, flags, remaining = [], [], 120000
    if reader.is_encrypted and not reader.decrypt(""):
        return {"pages": [], "risk_flags": ["pdf_encrypted"]}
    if len(reader.pages) > 80:
        flags.append("pdf_pages_truncated")
    for index, page in enumerate(reader.pages[:80], 1):
        text = " ".join((page.extract_text() or "").split())
        if len(text) > remaining:
            flags.append("pdf_text_truncated")
        pages.append({"page": index, "text": text[:remaining]})
        remaining -= len(pages[-1]["text"])
        if remaining <= 0:
            break
    if sum(len(page["text"]) for page in pages) < 60:
        flags.append("pdf_text_unavailable")
    return {"pages": pages, "risk_flags": flags}


if __name__ == "__main__":
    try:
        result = extract(sys.stdin.buffer.read(10000001))
    except Exception:
        result = {"pages": [], "risk_flags": ["pdf_parse_failed"]}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=True).encode("ascii"))
