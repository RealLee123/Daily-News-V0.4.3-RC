from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from daily_news.delivery.qq_markdown import (
    build_event_markdown_digest, split_qq_markdown, validate_news_markdown,
)
from daily_news.jobs import qq_markdown_preview_test


def sample_event(event_id: str, section: str, title: str, summary: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "id": event_id,
        "canonical_title": title,
        "summary": summary or f"{title}出现新的事实进展。市场与政策参与者正在评估后续影响。",
        "category": "markets",
        "region": "China" if section == "中国" else "Global",
        "importance": 85,
        "digest_section": section,
        "first_seen": now,
        "last_seen": now,
        "independent_source_count": 2,
        "official_source_count": 0,
        "articles": [{
            "id": f"article-{event_id}",
            "source_name": "Reuters",
            "source_group": "reuters",
            "canonical_url": f"https://example.com/news/{event_id}?from=test",
            "published_at": now,
        }],
    }


def test_digest_uses_clickable_titles_without_bare_urls():
    events = [
        sample_event("china", "中国", "中国央行调整政策"),
        sample_event("fed", "金融市场", "美联储释放新的利率信号"),
        sample_event("world", "全球政治", "全球政治事件出现进展"),
    ]
    markdown = build_event_markdown_digest(
        events,
        datetime(2026, 10, 4).date(),
        preview_label="【Daily News V0.4.3 Markdown Preview】",
    )
    validate_news_markdown(markdown)
    assert markdown.startswith("# Daily News\n\n2026-10-04")
    assert "【Daily News V0.4.3 Markdown Preview】" in markdown
    assert "### ① [中国央行调整政策](https://example.com/news/china?from=test)" in markdown
    assert "\nhttps://example.com" not in markdown
    assert "来源：Reuters" in markdown


def test_markdown_split_keeps_complete_titles_links_and_news_items():
    long_summary = "这是两到三句话的完整新闻摘要，用于检查手机QQ分段不会切断标题、链接或新闻条目。" * 4
    events = [
        sample_event(f"event-{index}", "中国" if index < 5 else "金融市场", f"完整新闻标题{index}", long_summary)
        for index in range(9)
    ]
    markdown = build_event_markdown_digest(events, datetime(2026, 10, 4).date())
    messages = split_qq_markdown(markdown, max_chars=850)
    assert len(messages) >= 2
    assert all(len(message) <= 850 for message in messages)
    for index in range(9):
        assert sum(f"完整新闻标题{index}" in message for message in messages) == 1
        full_link = f"[完整新闻标题{index}](https://example.com/news/event-{index}?from=test)"
        assert sum(full_link in message for message in messages) == 1


def test_markdown_preview_is_read_only_and_calls_native_markdown(monkeypatch):
    events = [
        sample_event("china", "中国", "中国央行调整政策"),
        sample_event("market", "金融市场", "美联储利率路径变化"),
        sample_event("world", "全球政治", "重要国际事件"),
    ]

    class FakeDB:
        def events_between(self, *args):
            return events

    class FakeBot:
        sent: list[str] = []

        def __init__(self, settings, db):
            pass

        async def push_many_markdown_while_online(self, messages):
            self.sent.extend(messages)
            return [{"id": str(index)} for index, _ in enumerate(messages)]

    snapshot = {
        "articles": 10, "latest_articles": 10, "embedded_articles": 10,
        "events": 3, "event_links": 10, "deliveries": 0, "digest_history": 0,
        "ai_decisions": 3, "ai_failures": 0, "pending_articles": 0, "pending_events": 0,
    }
    monkeypatch.setattr("daily_news.jobs.QQBot", FakeBot)
    monkeypatch.setattr("daily_news.jobs.database_snapshot", lambda db: dict(snapshot))
    monkeypatch.setattr(
        "daily_news.jobs.create_ai_provider",
        lambda settings: (_ for _ in ()).throw(AssertionError("Preview must not create an AI provider")),
    )
    settings = SimpleNamespace(
        timezone="Asia/Shanghai", qq_preview_hours=168,
        max_digest_events=24, qq_message_max_chars=1800,
    )
    result = asyncio.run(qq_markdown_preview_test(settings, FakeDB()))
    assert result["before"] == result["after"]
    assert FakeBot.sent[0].startswith("# Daily News")
    assert "【Daily News V0.4.3 Markdown Preview】" in FakeBot.sent[0]
    assert "[中国央行调整政策](https://example.com/news/china?from=test)" in FakeBot.sent[0]
