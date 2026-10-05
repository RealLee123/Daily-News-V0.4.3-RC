from __future__ import annotations

import html
import re
from hashlib import sha256
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_KEYS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source",
    "cmpid", "sref", "leadsource", "srnd",
}
UPDATE_PREFIX = re.compile(r"^(?:update\s*\d*|wrapup\s*\d*|recast|corrected?|breaking)\s*[-:—–]\s*", re.IGNORECASE)


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in TRACKING_KEYS]
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(sorted(query)), ""))


def normalize_text(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", value).strip()


def normalized_family_title(title: str) -> str:
    value = UPDATE_PREFIX.sub("", normalize_text(title)).lower()
    value = re.sub(r"\s+[-|｜]\s+[^-|｜]{2,50}$", "", value)
    return re.sub(r"[^\w\u4e00-\u9fff]+", " ", value).strip()


def make_content_hash(title: str, summary: str) -> str:
    return sha256(f"{normalize_text(title)}\n{normalize_text(summary)}".encode()).hexdigest()


def make_family_key(source_id: str, external_id: str, canonical_url: str, title: str) -> str:
    # Explicit UPDATE/CORRECTED/WRAPUP labels often get a new URL/GUID while remaining the
    # same source story. Otherwise prefer the feed's stable GUID, then canonical URL.
    stable = (f"title:{normalized_family_title(title)}" if UPDATE_PREFIX.match(normalize_text(title))
              else external_id.strip() or canonical_url or normalized_family_title(title))
    return sha256(f"{source_id}|{stable}".encode()).hexdigest()[:32]
