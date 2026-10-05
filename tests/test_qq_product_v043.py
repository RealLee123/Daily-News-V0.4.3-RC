import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from daily_news.delivery.qq_formatter import markdown_to_qq_plain, qq_messages_from_markdown
from daily_news.jobs import build_qq_preview_digest, qq_preview_test
from daily_news.pipeline.digest import digest_rank, prioritize_digest_events


def sample_event(event_id, title, category="politics", region="Global", importance=80):
    now = datetime.now(timezone.utc)
    return {
        "id": event_id, "canonical_title": title, "summary": f"{title} 的事件摘要。",
        "category": category, "region": region, "first_seen": now, "last_seen": now,
        "importance": importance, "scope": "international", "confidence": .9,
        "importance_reason": "test", "independent_source_count": 2,
        "official_source_count": 0, "articles": [{
            "id": f"a-{event_id}", "source_name": "Reuters", "source_group": "reuters",
            "source_kind": "news_media", "official": False, "primary_source": False,
            "title": title, "published_at": now,
            "canonical_url": f"https://example.com/{event_id}",
        }],
    }


def test_markdown_becomes_mobile_qq_plain_text_and_keeps_links():
    markdown = """# Daily News

## 今日重点

1. **美联储释放降息信号**

美国经济数据发生变化。

来源：[Reuters](https://www.reuters.com/example)
"""
    plain = markdown_to_qq_plain(markdown)
    assert plain.startswith("【Daily News】")
    assert "【今日重点】" in plain
    assert "美联储释放降息信号" in plain
    assert "https://www.reuters.com/example" in plain
    assert "#" not in plain
    assert "**" not in plain
    assert "[Reuters]" not in plain


def test_qq_split_prefers_sections_and_keeps_news_items_whole():
    long_summary = "这是用于测试手机QQ分段的完整新闻摘要。" * 10
    markdown = f"""# Daily News

## 今日重点

1. **重点新闻一**

{long_summary}

来源：Reuters

链接：
https://example.com/one

2. **重点新闻二**

{long_summary}

来源：BBC

链接：
https://example.com/two

## 中国

1. **中国政策新闻**

{long_summary}

来源：Bloomberg

链接：
https://example.com/china
"""
    messages = qq_messages_from_markdown(markdown, max_chars=650)
    assert len(messages) >= 2
    assert all(len(message) <= 650 for message in messages)
    assert sum("重点新闻一" in message for message in messages) == 1
    assert sum("重点新闻二" in message for message in messages) == 1
    assert sum("中国政策新闻" in message for message in messages) == 1
    china_message = next(message for message in messages if "中国政策新闻" in message)
    assert "【中国】" in china_message or "【中国（续）】" in china_message


def test_china_policy_and_market_events_receive_product_priority():
    events = [
        sample_event("world", "普通国际政治新闻", importance=90),
        sample_event("china-policy", "中国央行调整人民币货币政策", "economy", "China", 82),
        sample_event("fed", "美联储改变降息路径并影响债券市场", "markets", "United States", 84),
        sample_event("china-social", "中国某城市普通社会活动", "society", "China", 82),
    ]
    ranked = prioritize_digest_events(events)
    ids = [event["id"] for event in ranked]
    assert ids.index("china-policy") < ids.index("world")
    assert ids.index("fed") < ids.index("world")
    assert ids.index("china-social") > ids.index("world")
    assert digest_rank(events[1])[1] == "中国"
    assert digest_rank(events[2])[1] == "金融市场"


def test_preview_digest_has_fixed_header_sources_and_urls():
    markdown = build_qq_preview_digest([
        sample_event("china", "中国央行政策调整", "economy", "China", 90),
        sample_event("market", "美联储利率路径变化", "markets", "United States", 88),
    ])
    plain = markdown_to_qq_plain(markdown)
    assert plain.startswith("【Daily News V0.4.3 Preview Test】")
    assert "来源：Reuters" in plain
    assert "https://example.com/china" in plain


def test_qq_preview_calls_no_ai_embedding_or_formal_history(monkeypatch):
    events = [sample_event("preview", "中国央行政策调整", "economy", "China", 90)]

    class FakeDB:
        def events_between(self, *args):
            return events

    class FakeBot:
        sent = []

        def __init__(self, settings, db):
            pass

        async def push_many_while_online(self, messages):
            self.sent.extend(messages)
            return [{"id": str(index)} for index, _ in enumerate(messages)]

    snapshot = {
        "articles": 10, "latest_articles": 10, "embedded_articles": 10,
        "events": 2, "event_links": 10, "deliveries": 0, "digest_history": 0,
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
    result = asyncio.run(qq_preview_test(settings, FakeDB()))
    assert result["before"] == result["after"]
    assert FakeBot.sent[0].startswith("【Daily News V0.4.3 Preview Test】")
