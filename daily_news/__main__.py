from __future__ import annotations

import argparse
import asyncio

from daily_news.ai import AIProviderError
from daily_news.config import Settings
from daily_news.delivery import QQBot
from daily_news.jobs import (
    breaking_job, collect, digest_job, news_ai_test, print_database_audit,
    qq_markdown_preview_test, qq_preview_test, run_news_job,
)
from daily_news.maintenance import biweekly_cleanup, format_report
from daily_news.migrate import format_migration_report, migrate
from daily_news.postgres import db_test, normalize_url, setup_postgres
from daily_news.storage import Database


def _run_command(args, settings, db) -> None:
    if args.command == "init-db":
        print("数据库初始化完成")
        return
    if args.command in {"qq-smoke", "qq-push-test", "qq-preview-test", "qq-markdown-preview-test", "run-news", "morning", "evening", "breaking", "news-scan"}:
        settings.require_qq()
    if args.command == "qq-smoke":
        asyncio.run(QQBot(settings, db).smoke_test())
    elif args.command == "qq-push-test":
        asyncio.run(QQBot(settings, db).push_while_online("主动推送测试"))
        print("主动推送测试成功")
    elif args.command == "qq-preview-test":
        asyncio.run(qq_preview_test(settings, db))
    elif args.command == "qq-markdown-preview-test":
        asyncio.run(qq_markdown_preview_test(settings, db))
    elif args.command == "db-audit":
        print_database_audit(db)
    elif args.command == "db-maintenance":
        # Retention sweep only: never touches AI, QQ or the pipeline.
        print(format_report(biweekly_cleanup(db, dry_run=args.dry_run, vacuum=args.vacuum)))
    elif args.command == "run-news":
        asyncio.run(run_news_job(settings, db))
    elif args.command == "collect":
        asyncio.run(collect(settings, db))
    elif args.command == "news-ai-test":
        asyncio.run(news_ai_test(settings, db))
    elif args.command in {"morning", "evening"}:
        asyncio.run(digest_job(settings, db, args.command))
    elif args.command in {"news-scan", "breaking"}:
        # Same job: ingest, embed, cluster, assess, then alert on breaking Events.
        # "news-scan" is the name of the two-hourly schedule; "breaking" is kept as an
        # alias for older scripts.
        asyncio.run(breaking_job(settings, db))


def main() -> None:
    parser = argparse.ArgumentParser(prog="daily-news")
    parser.add_argument("command", choices=[
        "init-db", "qq-smoke", "qq-push-test", "qq-preview-test", "qq-markdown-preview-test", "db-audit",
        "db-maintenance",
        "collect", "news-ai-test", "run-news", "morning", "evening", "breaking",
        "news-scan",
        "setup-postgres", "db-test", "migrate-db",
    ])
    parser.add_argument("--dry-run", action="store_true", help="仅用于 db-maintenance：只统计，不修改数据库")
    parser.add_argument("--vacuum", action="store_true", help="仅用于 db-maintenance：清理后 VACUUM 回收空间")
    parser.add_argument("--database-url", default=None, help="覆盖 DATABASE_URL（默认取 Settings）")
    parser.add_argument("--source", default="sqlite:///news.db",
                        help="仅用于 migrate-db：SQLite 源，只读打开（默认 sqlite:///news.db）")
    parser.add_argument("--force", action="store_true",
                        help="仅用于 migrate-db：目标库非空时先清空目标表")
    args = parser.parse_args()
    settings = Settings()

    # setup-postgres comes first and touches no database: it is how DATABASE_URL gets
    # configured in the first place, so it cannot require one.
    if args.command == "setup-postgres":
        setup_postgres(args.database_url)
        return

    # Pin the driver for every command, not just db-test/migrate-db: a bare
    # postgresql:// resolves to psycopg2 on SQLAlchemy 2.0 and psycopg3 on 2.1.
    url = normalize_url(args.database_url or settings.require_database_url())

    if args.command == "migrate-db":
        report = migrate(args.source, url, force=args.force)
        print(format_migration_report(report, args.source, url))
        return

    if args.command == "db-test":
        raise SystemExit(db_test(url))

    db = Database(url)
    db.initialize()

    try:
        _run_command(args, settings, db)
    except AIProviderError as exc:
        print("\n[AI] 本次 AI 处理已安全停止；已入库新闻与 pending/retry 状态会保留。")
        print(exc)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
