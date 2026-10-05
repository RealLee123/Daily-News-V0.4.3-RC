from __future__ import annotations

from sqlalchemy import text


def database_snapshot(db) -> dict[str, int]:
    """Read-only operational counters used by preview/verify commands."""
    queries = {
        "articles": "SELECT COUNT(*) FROM article_versions_v2",
        "latest_articles": "SELECT COUNT(*) FROM article_versions_v2 WHERE is_latest=true",
        "embedded_articles": "SELECT COUNT(*) FROM article_versions_v2 WHERE embedding IS NOT NULL",
        "events": "SELECT COUNT(*) FROM news_events_v2",
        "event_links": "SELECT COUNT(*) FROM event_articles_v2",
        "deliveries": "SELECT COUNT(*) FROM deliveries",
        "digest_history": "SELECT COUNT(*) FROM digest_event_history_v3",
        "ai_decisions": "SELECT COUNT(*) FROM ai_decisions_v2",
        "ai_failures": "SELECT COUNT(*) FROM ai_failures_v3",
        "pending_articles": """SELECT COUNT(*) FROM article_versions_v2
            WHERE is_latest=true AND cluster_status IN ('pending','exact_deduped','embedded')""",
        "pending_events": """SELECT COUNT(*) FROM news_events_v2
            WHERE assessment_status IN ('pending','retry')""",
    }
    with db.engine.connect() as connection:
        return {name: int(connection.execute(text(query)).scalar_one()) for name, query in queries.items()}


def format_database_snapshot(snapshot: dict[str, int]) -> str:
    labels = {
        "articles": "文章总数", "latest_articles": "最新文章版本",
        "embedded_articles": "已有 Embedding", "events": "Event 总数",
        "event_links": "Article→Event 关联", "deliveries": "正式 delivery 记录",
        "digest_history": "正式 digest 历史", "ai_decisions": "AI 决策记录",
        "ai_failures": "AI 失败记录", "pending_articles": "待聚类文章",
        "pending_events": "待重要性评估 Event",
    }
    return "\n".join(f"{labels[key]}：{snapshot[key]}" for key in labels)
