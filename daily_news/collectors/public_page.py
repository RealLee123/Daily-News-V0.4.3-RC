from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import httpx

from daily_news.models import Article
from daily_news.pipeline.article_identity import canonicalize_url, make_content_hash, make_family_key, normalize_text
from daily_news.source_registry import SourceDefinition


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_date(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    value = value.strip()
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            result = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return datetime.now(timezone.utc)
    return result.replace(tzinfo=result.tzinfo or timezone.utc).astimezone(timezone.utc)


def _article(source: SourceDefinition, title: str, url: str, published_at: datetime, summary: str = "") -> Article:
    title, summary = normalize_text(title), normalize_text(summary)
    canonical = canonicalize_url(url)
    return Article(
        **source.article_metadata(), title=title, url=url, canonical_url=canonical,
        external_id=canonical, content_hash=make_content_hash(title, summary),
        family_key=make_family_key(source.source_id, canonical, canonical, title),
        published_at=published_at, summary=summary,
    )


async def collect_news_sitemap(client: httpx.AsyncClient, source: SourceDefinition) -> list[Article]:
    """Read an official public news sitemap; never fetch article bodies or bypass access controls."""
    response = await client.get(source.url)
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    roots = [root]
    if _local(root.tag) == "sitemapindex":
        locations = []
        for sitemap in list(root):
            location = next((child.text.strip() for child in sitemap if _local(child.tag) == "loc" and child.text), "")
            if location:
                locations.append(location)
        max_sitemaps = int((source.options or {}).get("max_sitemaps", 2))
        roots = []
        for location in locations[:max_sitemaps]:
            child = await client.get(location)
            child.raise_for_status()
            roots.append(ElementTree.fromstring(child.content))

    allowed_hosts = set((source.options or {}).get("allowed_hosts", []))
    result: list[Article] = []
    for sitemap in roots:
        for node in sitemap.iter():
            if _local(node.tag) != "url":
                continue
            url = next((child.text.strip() for child in list(node) if _local(child.tag) == "loc" and child.text), "")
            title = next((child.text.strip() for child in node.iter() if _local(child.tag) == "title" and child.text), "")
            published = next((child.text.strip() for child in node.iter()
                              if _local(child.tag) in {"publication_date", "lastmod"} and child.text), "")
            if not url or not title or (allowed_hosts and urlsplit(url).hostname not in allowed_hosts):
                continue
            result.append(_article(source, title, url, _parse_date(published)))
    limit = int((source.options or {}).get("max_articles", 100))
    return sorted(result, key=lambda item: item.published_at, reverse=True)[:limit]


class _PublicPageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.anchors: list[tuple[str, str]] = []
        self.json_ld: list[str] = []
        self._href: str | None = None
        self._text: list[str] = []
        self._script = False
        self._script_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self._href, self._text = attrs["href"], []
        elif tag == "script" and attrs.get("type", "").lower() == "application/ld+json":
            self._script, self._script_text = True, []

    def handle_data(self, data):
        if self._href:
            self._text.append(data)
        if self._script:
            self._script_text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.anchors.append((self._href, normalize_text(" ".join(self._text))))
            self._href, self._text = None, []
        elif tag == "script" and self._script:
            self.json_ld.append("".join(self._script_text))
            self._script, self._script_text = False, []


def _walk_json(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


async def collect_public_page(client: httpx.AsyncClient, source: SourceDefinition) -> list[Article]:
    response = await client.get(source.url)
    response.raise_for_status()
    parser = _PublicPageParser()
    parser.feed(response.text)
    options = source.options or {}
    allowed_hosts = set(options.get("allowed_hosts", []))
    prefixes = tuple(options.get("path_prefixes", []))
    candidates: dict[str, Article] = {}

    if source.source_group == "white_house":
        card_pattern = re.compile(
            r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>\s*</h2>.*?'
            r'<time[^>]+datetime="([^"]+)"', re.IGNORECASE | re.DOTALL,
        )
        for href, title, published in card_pattern.findall(response.text):
            absolute = urljoin(source.url, href)
            candidates[canonicalize_url(absolute)] = _article(source, title, absolute, _parse_date(published))

    for raw in parser.json_ld:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for item in _walk_json(payload):
            if item.get("@type") not in {"NewsArticle", "Article", "ReportageNewsArticle"}:
                continue
            url = item.get("url") or item.get("mainEntityOfPage")
            if isinstance(url, dict):
                url = url.get("@id")
            title = item.get("headline") or item.get("name")
            if url and title:
                absolute = urljoin(source.url, url)
                candidates[canonicalize_url(absolute)] = _article(
                    source, title, absolute, _parse_date(item.get("datePublished") or item.get("dateModified")),
                    item.get("description", ""),
                )

    for href, title in ([] if source.source_group == "white_house" else parser.anchors):
        absolute = urljoin(source.url, href)
        parts = urlsplit(absolute)
        if len(title) < 12 or (allowed_hosts and parts.hostname not in allowed_hosts):
            continue
        if prefixes and not parts.path.startswith(prefixes):
            continue
        canonical = canonicalize_url(absolute)
        candidates.setdefault(canonical, _article(source, title, absolute, datetime.now(timezone.utc)))

    return list(candidates.values())[:int(options.get("max_articles", 50))]
