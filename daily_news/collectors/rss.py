from __future__ import annotations

from datetime import datetime, timezone

import feedparser
import httpx

from daily_news.models import Article
from daily_news.pipeline.article_identity import canonicalize_url, make_content_hash, make_family_key, normalize_text
from daily_news.source_registry import SourceDefinition


def _date(entry) -> datetime:
    parsed = entry.get("updated_parsed") or entry.get("published_parsed")
    return datetime(*parsed[:6], tzinfo=timezone.utc) if parsed else datetime.now(timezone.utc)


async def collect_rss(client: httpx.AsyncClient, source: SourceDefinition) -> list[Article]:
    response = await client.get(source.url)
    response.raise_for_status()
    parsed = feedparser.parse(response.content)
    result: list[Article] = []
    for entry in parsed.entries[:100]:
        url = str(entry.get("link", "")).strip()
        title = normalize_text(str(entry.get("title", "")))
        summary = normalize_text(str(entry.get("summary", "") or entry.get("description", "")))
        if not title or not url:
            continue
        canonical_url = canonicalize_url(url)
        external_id = str(entry.get("id") or entry.get("guid") or canonical_url).strip()
        result.append(Article(
            **source.article_metadata(), title=title, url=url, canonical_url=canonical_url,
            external_id=external_id, content_hash=make_content_hash(title, summary),
            family_key=make_family_key(source.source_id, external_id, canonical_url, title),
            published_at=_date(entry), summary=summary,
        ))
    return result
