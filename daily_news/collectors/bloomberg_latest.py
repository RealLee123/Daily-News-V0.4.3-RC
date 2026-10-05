from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from daily_news.models import Article
from daily_news.pipeline.article_identity import (
    canonicalize_url,
    make_content_hash,
    make_family_key,
    normalize_text,
)
from daily_news.source_registry import SourceDefinition


SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
NEWS_NS = "http://www.google.com/schemas/sitemap-news/0.9"


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)


class BloombergLatestAdapter:
    """Read Bloomberg's official rolling news sitemap.

    The endpoint is advertised by Bloomberg's public robots.txt as the latest
    news sitemap.  It is a discovery source only: article bodies, cookies,
    login state and paywalled content are never requested.
    """

    def __init__(self, source: SourceDefinition):
        self.source = source

    async def collect(self, client: httpx.AsyncClient) -> list[Article]:
        response = await client.get(
            self.source.url,
            headers={"Accept": "application/xml,text/xml;q=0.9,*/*;q=0.1"},
        )
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
        options = self.source.options or {}
        allowed_hosts = set(options.get("allowed_hosts", []))
        path_prefixes = tuple(options.get("path_prefixes", []))
        articles: list[Article] = []

        for node in root.findall(f"{{{SITEMAP_NS}}}url"):
            raw_url = node.findtext(f"{{{SITEMAP_NS}}}loc", "").strip()
            news = node.find(f"{{{NEWS_NS}}}news")
            title = news.findtext(f"{{{NEWS_NS}}}title", "").strip() if news is not None else ""
            published = (
                news.findtext(f"{{{NEWS_NS}}}publication_date", "").strip()
                if news is not None else ""
            )
            if not published:
                published = node.findtext(f"{{{SITEMAP_NS}}}lastmod", "").strip()
            if not raw_url or not title or not published:
                continue

            parts = urlsplit(raw_url)
            if allowed_hosts and parts.hostname not in allowed_hosts:
                continue
            if path_prefixes and not parts.path.startswith(path_prefixes):
                continue
            try:
                published_at = _parse_timestamp(published)
            except ValueError:
                # A broken timestamp should not be silently replaced by fetched_at.
                continue

            title = normalize_text(title)
            canonical = canonicalize_url(raw_url)
            articles.append(Article(
                **self.source.article_metadata(),
                title=title,
                url=raw_url,
                canonical_url=canonical,
                external_id=canonical,
                content_hash=make_content_hash(title, ""),
                family_key=make_family_key(self.source.source_id, canonical, canonical, title),
                published_at=published_at,
                summary="",
            ))

        limit = int(options.get("max_articles", 100))
        articles.sort(key=lambda item: item.published_at, reverse=True)
        return articles[:limit]


async def collect_bloomberg_latest(
    client: httpx.AsyncClient, source: SourceDefinition
) -> list[Article]:
    return await BloombergLatestAdapter(source).collect(client)
