from __future__ import annotations

import asyncio

import httpx

from daily_news.collectors.bloomberg_latest import collect_bloomberg_latest
from daily_news.collectors.public_page import collect_news_sitemap, collect_public_page
from daily_news.collectors.rss import collect_rss
from daily_news.collectors.x_api import collect_x_user_timeline
from daily_news.source_registry import SourceDefinition, SourceRegistry


async def _collect_one(client: httpx.AsyncClient, source: SourceDefinition, settings):
    if source.fetch_method == "rss":
        return await collect_rss(client, source)
    if source.fetch_method == "news_sitemap":
        return await collect_news_sitemap(client, source)
    if source.fetch_method == "bloomberg_latest_sitemap":
        return await collect_bloomberg_latest(client, source)
    if source.fetch_method == "public_page":
        return await collect_public_page(client, source)
    if source.fetch_method == "x_api_v2":
        return await collect_x_user_timeline(client, source, getattr(settings, "x_bearer_token", ""))
    raise ValueError(f"unsupported fetch_method: {source.fetch_method}")


async def collect_all(sources_file, timeout: float, settings=None) -> tuple[list, list[str]]:
    registry = SourceRegistry.load(sources_file, settings)
    enabled = registry.enabled()
    print(f"[collect] 开始抓取 {len(enabled)} 个新闻源（并发）")
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True,
        headers={"User-Agent": "DailyNewsAgent/0.4.2 (+personal metadata reader)"},
    ) as client:
        results = await asyncio.gather(
            *[_collect_one(client, source, settings) for source in enabled], return_exceptions=True,
        )
    articles, errors = [], []
    for source, result in zip(enabled, results):
        if isinstance(result, Exception):
            errors.append(f"{source.source_name}: {type(result).__name__}: {result}")
            print(f"[collect] {source.source_name} 失败：{type(result).__name__}")
        else:
            articles.extend(result)
            print(f"[collect] {source.source_name} 完成 {len(result)} 篇")
    return articles, errors
