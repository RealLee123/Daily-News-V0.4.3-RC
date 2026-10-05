"""Storage maintenance for the Daily News agent.

This module only ever removes rows the pipeline can no longer read. It never creates,
alters or drops a table, and it never adds a column -- the schema in
``daily_news.storage.database.SCHEMA`` is read-only truth here.

The statements below were written against the real schema, re-verified with
``PRAGMA table_info`` / ``PRAGMA index_list`` / ``PRAGMA foreign_key_list`` on the
V0.4.3 SQLite database, and they run unchanged on PostgreSQL:

* 8 tables: ``article_versions_v2``, ``news_events_v2``, ``event_articles_v2``,
  ``ai_decisions_v2``, ``ai_failures_v3``, ``settings``, ``deliveries``,
  ``digest_event_history_v3``.
* **zero foreign keys** -- every relationship is by convention, so dependent rows
  have to be removed explicitly. Nothing cascades.
* ``event_articles_v2(event_id, article_id)`` is the only Article to Event link.
* Primary keys used for deletion: ``article_versions_v2.id``,
  ``news_events_v2.id``, ``ai_decisions_v2.id``, ``ai_failures_v3.id``,
  ``deliveries.id``, and the composite ``digest_event_history_v3(edition_id, event_id)``.
* Timestamps are timezone-aware strings (``2026-10-04 05:21:11+00:00``) and booleans
  are 0/1 on SQLite.
* Embeddings are stored inline as JSON text -- ``article_versions_v2.embedding``
  (nullable) and ``news_events_v2.embedding`` (NOT NULL). There is no embedding
  table, so an embedding is deleted with the row that owns it.

Retention policy (this is a personal briefing feed, not an archive)::

    Article        14 days   and not referenced by an Event
    Event          28 days   (its event_articles_v2 rows go with it)
    Embedding      with its Article / Event; a vector is never rewritten or refreshed
    AI logs        90 days   (ai_decisions_v2, ai_failures_v3)
    Delivery       30 days   (deliveries, digest_event_history_v3)

``is_latest`` deliberately plays no part in the Article rule. Version currency is not
an expiry signal: an old current version is exactly as disposable as an old superseded
one. What protects a story is its Event, and Events outlive Articles (28 days vs 14),
so the lifecycle is Event expires -> its article links are released -> the articles
become eligible and are removed on the next sweep.

PostgreSQL readiness
--------------------
SQLite and PostgreSQL disagree on how booleans and datetimes are written, so every
comparison here binds a Python value instead of embedding a SQL literal (a literal
``false`` breaks SQLite, a literal ``0`` breaks PostgreSQL). Deletions are always
"select the primary keys, then ``DELETE ... WHERE <key> IN (...)``" in bounded
batches, never ``DELETE ... LIMIT``. No ``PRAGMA``, ``rowid``, ``INSERT OR REPLACE``,
``strftime()`` or ``datetime('now')`` appears anywhere.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from daily_news.audit import database_snapshot

# --------------------------------------------------------------------------- #
# Retention policy
# --------------------------------------------------------------------------- #

ARTICLE_RETENTION_DAYS = 14
"""Normal news is kept for two weeks."""

EVENT_RETENTION_DAYS = 28
"""Events are kept for four weeks. They deliberately outlive their articles."""

DELIVERY_RETENTION_DAYS = 30
"""Delivery and digest history is kept for a month."""

AI_LOG_RETENTION_DAYS = 90
"""``ai_decisions_v2`` / ``ai_failures_v3`` are diagnostics, kept for a quarter."""

BATCH_SIZE = 500
"""Rows per statement. Far below SQLite's bind-variable ceiling."""


@dataclass(slots=True)
class MaintenancePolicy:
    """Retention knobs. The defaults are the agreed policy."""

    article_retention_days: int = ARTICLE_RETENTION_DAYS
    event_retention_days: int = EVENT_RETENTION_DAYS
    delivery_retention_days: int = DELIVERY_RETENTION_DAYS
    ai_log_retention_days: int = AI_LOG_RETENTION_DAYS
    compact_orphan_embeddings: bool = True
    """Clear embeddings no code path can read any more (see the stage docstring).

    On by default: without it, vectors that the pipeline can never consult again
    accumulate indefinitely, which on a real database is the bulk of the file.
    Turn it off with ``--no-compact-orphan-embeddings`` if you would rather keep the
    bytes; deleting an Article or an Event always takes its own embedding with it
    either way.
    """

    def cutoffs(self, now: datetime) -> dict[str, datetime]:
        return {
            "article": now - timedelta(days=self.article_retention_days),
            "event": now - timedelta(days=self.event_retention_days),
            "delivery": now - timedelta(days=self.delivery_retention_days),
            "ai_log": now - timedelta(days=self.ai_log_retention_days),
        }


@dataclass(slots=True)
class StageResult:
    """What one cleanup stage found, and -- outside dry-run -- what it removed."""

    name: str
    candidates: int = 0
    affected: int = 0
    note: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "candidates": self.candidates,
                "affected": self.affected, "note": self.note}


@dataclass(slots=True)
class CleanupReport:
    dry_run: bool
    now: datetime
    cutoffs: dict[str, datetime]
    stages: list[StageResult] = field(default_factory=list)
    before: dict[str, int] = field(default_factory=dict)
    after: dict[str, int] = field(default_factory=dict)

    @property
    def total_affected(self) -> int:
        return sum(stage.affected for stage in self.stages)

    def stage(self, name: str) -> StageResult:
        for item in self.stages:
            if item.name == name:
                return item
        raise KeyError(name)

    def to_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "now": self.now.isoformat(),
            "cutoffs": {key: value.isoformat() for key, value in self.cutoffs.items()},
            "stages": [item.to_dict() for item in self.stages],
            "before": self.before,
            "after": self.after,
        }


# --------------------------------------------------------------------------- #
# portable primitives
# --------------------------------------------------------------------------- #

def _batches(values: list, size: int = BATCH_SIZE):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _placeholders(prefix: str, values: list) -> tuple[str, dict]:
    names = ",".join(":" + prefix + str(index) for index in range(len(values)))
    params = {prefix + str(index): value for index, value in enumerate(values)}
    return names, params


def _select_ids(db, sql: str, params: dict) -> list:
    with db.engine.connect() as connection:
        return [row[0] for row in connection.execute(text(sql), params)]


def _count(db, sql: str, params: dict) -> int:
    with db.engine.connect() as connection:
        return int(connection.execute(text(sql), params).scalar_one())


def _delete_where_in(db, table: str, column: str, ids: list) -> int:
    """Delete by key in bounded batches. No ``DELETE ... LIMIT``, so it is portable."""
    removed = 0
    for batch in _batches(ids):
        names, params = _placeholders("p", batch)
        with db.engine.begin() as connection:
            result = connection.execute(
                text("DELETE FROM " + table + " WHERE " + column + " IN (" + names + ")"), params
            )
            removed += result.rowcount or 0
    return removed


def _count_where_in(db, table: str, column: str, ids: list) -> int:
    """Count rows that a matching ``_delete_where_in`` would remove."""
    total = 0
    for batch in _batches(ids):
        names, params = _placeholders("p", batch)
        total += _count(db, "SELECT COUNT(*) FROM " + table + " WHERE " + column + " IN (" + names + ")", params)
    return total


def _vacuum(db) -> None:
    """Compact the database file. Has to run outside a transaction."""
    with db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("VACUUM"))


# --------------------------------------------------------------------------- #
# stage 1 -- events and their links
# --------------------------------------------------------------------------- #

def _select_expired_events(db, cutoff: datetime) -> list[str]:
    """Every event whose newest article is older than the retention window."""
    return _select_ids(
        db, "SELECT id FROM news_events_v2 WHERE last_seen < :cutoff ORDER BY last_seen ASC",
        {"cutoff": cutoff},
    )


def _cleanup_events(db, report: CleanupReport) -> None:
    """Delete events past the retention window, links first.

    ``event_articles_v2`` has no foreign key to ``news_events_v2``, so deleting an
    event would silently leave orphaned link rows behind. The links are therefore
    removed explicitly, and in the same run, before the event itself.

    The age clock is ``last_seen`` (the newest article folded into the event), so a
    story that is still developing is not aged out from under itself.
    """
    cutoff = report.cutoffs["event"]
    ids = _select_expired_events(db, cutoff)

    events = StageResult("events", len(ids), 0, "last_seen < " + cutoff.date().isoformat())
    links = StageResult("event_links", 0, 0, "过期 Event 的 Article→Event 关联，同步删除不留孤儿")

    if ids:
        links.candidates = _count_where_in(db, "event_articles_v2", "event_id", ids)
        if not report.dry_run:
            links.affected = _delete_where_in(db, "event_articles_v2", "event_id", ids)
            events.affected = _delete_where_in(db, "news_events_v2", "id", ids)

    report.stages.append(events)
    report.stages.append(links)


# --------------------------------------------------------------------------- #
# stage 2 -- articles
# --------------------------------------------------------------------------- #

def _select_expired_articles(db, cutoff: datetime) -> list[str]:
    """Article rows that are safe to drop, i.e. both conditions hold.

    * ``published_at`` is older than the retention window, **and**
    * no Event references it. The evidence chain always wins: an article that is
      still cited by an Event is kept however old it is, which is what ``NOT EXISTS``
      enforces.

    ``is_latest`` is not consulted -- an old current version is deleted exactly like
    an old superseded version. Importance is the Event's job, not the Article's.
    """
    return _select_ids(db, """
        SELECT a.id FROM article_versions_v2 a
        WHERE a.published_at < :cutoff
          AND NOT EXISTS (SELECT 1 FROM event_articles_v2 ea WHERE ea.article_id = a.id)
        ORDER BY a.published_at ASC
    """, {"cutoff": cutoff})


def _cleanup_articles(db, report: CleanupReport) -> None:
    """Delete expired articles that no Event cites.

    An article's embedding lives in a column on the same row, so deleting the row
    deletes its embedding with it. Nothing in this stage writes an embedding, which
    is why maintenance can never cause one to be regenerated.
    """
    cutoff = report.cutoffs["article"]
    ids = _select_expired_articles(db, cutoff)
    stage = StageResult("articles", len(ids), 0,
                        "published_at < " + cutoff.date().isoformat() + " 且无 Event 引用")
    if ids and not report.dry_run:
        stage.affected = _delete_where_in(db, "article_versions_v2", "id", ids)
    report.stages.append(stage)


# --------------------------------------------------------------------------- #
# stage 3 -- orphaned embeddings
# --------------------------------------------------------------------------- #

def _cleanup_orphan_embeddings(db, report: CleanupReport) -> None:
    """Clear embeddings that no code path can read any more. On by default.

    Why this is safe, and why it can never trigger a re-embed:

    * ``Database.pending_articles`` is the only reader of article embeddings, and it
      selects ``is_latest = true AND cluster_status IN ('pending','exact_deduped','embedded')``.
    * ``Database.save_article_embeddings`` only writes rows whose ``cluster_status``
      is in that same set.
    * ``pipeline.event_clusterer.cluster_pending`` therefore only ever embeds and
      merges rows in that set.

    So an embedding is unreachable exactly when the row is a superseded version
    (``is_latest = false``) or is already ``clustered``. ``news_events_v2.embedding``
    is a NOT NULL column, so an event's vector simply disappears with its row and is
    never cleared separately.

    The vector of a row that remains reachable is never touched, and no surviving
    embedding is ever rewritten or refreshed: this stage only ever turns the whole
    four-column group (``embedding`` / ``embedding_model`` / ``embedding_dimensions``
    / ``embedded_at``) off together, for rows the pipeline can no longer consult.
    """
    ids = _select_ids(db, """
        SELECT id FROM article_versions_v2
        WHERE embedding IS NOT NULL
          AND (is_latest = :not_latest OR cluster_status = :clustered)
    """, {"not_latest": False, "clustered": "clustered"})
    stage = StageResult("orphan_embeddings", len(ids), 0,
                        "is_latest = false 或 cluster_status = 'clustered'；只清不可达向量，不重写向量")
    if ids and not report.dry_run:
        cleared = 0
        for batch in _batches(ids):
            names, params = _placeholders("p", batch)
            with db.engine.begin() as connection:
                result = connection.execute(text(
                    "UPDATE article_versions_v2 "
                    "SET embedding=NULL, embedding_model=NULL, embedding_dimensions=NULL, embedded_at=NULL "
                    "WHERE id IN (" + names + ")"
                ), params)
                cleared += result.rowcount or 0
        stage.affected = cleared
    report.stages.append(stage)


# --------------------------------------------------------------------------- #
# stage 4 -- AI diagnostics
# --------------------------------------------------------------------------- #

def _cleanup_ai_logs(db, report: CleanupReport) -> None:
    """Trim ``ai_decisions_v2`` and ``ai_failures_v3`` to 90 days.

    Both are append-only diagnostics keyed by ``id`` with a ``created_at`` stamp.
    Nothing reads them back to make a decision, so trimming them cannot change any
    pipeline behaviour.
    """
    cutoff = report.cutoffs["ai_log"]
    label = cutoff.date().isoformat()
    for table, stage_name in (("ai_decisions_v2", "ai_decisions"), ("ai_failures_v3", "ai_failures")):
        params = {"cutoff": cutoff}
        ids = _select_ids(db, "SELECT id FROM " + table + " WHERE created_at < :cutoff", params)
        stage = StageResult(stage_name, len(ids), 0, "created_at < " + label)
        if ids and not report.dry_run:
            stage.affected = _delete_where_in(db, table, "id", ids)
        report.stages.append(stage)


# --------------------------------------------------------------------------- #
# stage 5 -- delivery and digest history
# --------------------------------------------------------------------------- #

def _cleanup_delivery_history(db, report: CleanupReport) -> None:
    """Trim ``deliveries`` and ``digest_event_history_v3`` to 30 days.

    Both guard windows measured in hours -- ``deliveries.id`` is ``"{kind}:{date}"``
    for editions and ``"breaking:{event_id}"`` for alerts, and
    ``digest_event_history_v3`` answers "has this event already run, and was there a
    substantive update". A month is far longer than any window either is consulted
    for, so trimming them cannot cause a repeat push.
    """
    cutoff = report.cutoffs["delivery"]
    label = cutoff.date().isoformat()

    rows = _select_ids(db, "SELECT id FROM deliveries WHERE delivered_at < :cutoff", {"cutoff": cutoff})
    stage = StageResult("deliveries", len(rows), 0, "delivered_at < " + label)
    if rows and not report.dry_run:
        stage.affected = _delete_where_in(db, "deliveries", "id", rows)
    report.stages.append(stage)

    editions = _select_ids(db, """
        SELECT DISTINCT edition_id FROM digest_event_history_v3 WHERE included_at < :cutoff
    """, {"cutoff": cutoff})
    history = StageResult("digest_history", 0, 0, "included_at < " + label)
    if editions:
        names, params = _placeholders("p", editions)
        history.candidates = _count(
            db, "SELECT COUNT(*) FROM digest_event_history_v3 WHERE edition_id IN (" + names + ")", params
        )
        if not report.dry_run:
            with db.engine.begin() as connection:
                result = connection.execute(text(
                    "DELETE FROM digest_event_history_v3 WHERE edition_id IN (" + names + ")"
                ), params)
                history.affected = result.rowcount or 0
    report.stages.append(history)


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #

def biweekly_cleanup(db, *, dry_run: bool = False, now: datetime | None = None,
                     policy: MaintenancePolicy | None = None, vacuum: bool = False) -> CleanupReport:
    """Run the retention sweep. Safe to call repeatedly.

    Args:
        db: a ``daily_news.storage.Database``. Any SQLAlchemy dialect works; SQLite
            and PostgreSQL take the same code path.
        dry_run: when true every stage only counts candidates and the database is
            left byte-for-byte untouched.
        now: override the reference instant (tests, reproducible runs). Defaults to
            the current UTC time.
        policy: retention knobs, see :class:`MaintenancePolicy`.
        vacuum: run ``VACUUM`` after the sweep. SQLite only marks freed pages as
            reusable -- the file itself shrinks only after a vacuum. Off by default:
            it rewrites the whole database and needs free disk equal to the current
            file size. Future inserts reuse the freed pages either way.

    Returns:
        :class:`CleanupReport` with per-stage candidate/affected counts and
        before/after operational snapshots.

    Order matters: expiring an Event releases its links, which is what makes its
    articles eligible in the very same run.

    Embeddings are handled on both sides of that lifecycle: deleting an Article or an
    Event removes the embedding stored on its row, and the default sweep additionally
    clears vectors that are still stored but unreachable (superseded versions and
    already-clustered rows). No vector is ever rewritten, recomputed or refreshed, so
    maintenance cannot make the pipeline re-embed anything.
    """
    policy = policy or MaintenancePolicy()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    report = CleanupReport(dry_run=dry_run, now=now, cutoffs=policy.cutoffs(now))
    report.before = database_snapshot(db)

    _cleanup_events(db, report)
    _cleanup_articles(db, report)
    if policy.compact_orphan_embeddings:
        _cleanup_orphan_embeddings(db, report)
    _cleanup_ai_logs(db, report)
    _cleanup_delivery_history(db, report)

    if vacuum and not dry_run:
        _vacuum(db)

    report.after = database_snapshot(db)
    return report


# --------------------------------------------------------------------------- #
# reporting / CLI
# --------------------------------------------------------------------------- #

_STAGE_LABELS = {
    "events": "过期 Event",
    "event_links": "Event 关联记录",
    "articles": "过期 Article",
    "orphan_embeddings": "孤立 Embedding",
    "ai_decisions": "AI 决策日志",
    "ai_failures": "AI 失败日志",
    "deliveries": "过期 delivery 记录",
    "digest_history": "过期 digest 历史",
}

_SNAPSHOT_LABELS = {
    "articles": "文章总数",
    "latest_articles": "当前版本文章",
    "embedded_articles": "已有 Embedding",
    "events": "Event 总数",
    "event_links": "Article→Event 关联",
    "deliveries": "delivery 记录",
    "digest_history": "digest 历史",
    "ai_decisions": "AI 决策记录",
    "ai_failures": "AI 失败记录",
    "pending_articles": "待聚类文章",
    "pending_events": "待评估 Event",
}


def format_report(report: CleanupReport) -> str:
    mode = "DRY-RUN（不修改数据库）" if report.dry_run else "实际执行"
    boundary = "  ".join(
        name + " < " + report.cutoffs[key].date().isoformat()
        for name, key in (("Article", "article"), ("Event", "event"),
                          ("AI 日志", "ai_log"), ("Delivery/digest", "delivery"))
    )
    lines = [
        "=== Daily News 数据库维护（" + mode + "）===",
        "参考时间：" + report.now.isoformat(timespec="seconds"),
        "保留边界：" + boundary,
        "",
        "阶段                       命中      删除/清理",
        "-" * 46,
    ]
    for stage in report.stages:
        lines.append("%-22s%8d%12d" % (_STAGE_LABELS.get(stage.name, stage.name), stage.candidates, stage.affected))
    lines.append("-" * 46)
    lines.append("%-22s%8s%12d" % ("合计", "", report.total_affected))
    lines.append("")
    for stage in report.stages:
        if stage.note:
            lines.append("  · " + _STAGE_LABELS.get(stage.name, stage.name) + "：" + stage.note)

    lines.extend(["", "数据库快照（前 → 后）："])
    for key, before in report.before.items():
        after = report.after.get(key, 0)
        marker = "" if before == after else "   <-- 变化"
        lines.append("  %-16s%8d → %8d%s" % (_SNAPSHOT_LABELS.get(key, key), before, after, marker))

    if report.dry_run:
        lines.extend(["", "这是 dry-run：以上数字是预计删除/清理数量，数据库未做任何修改。"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="daily-news-maintenance", description="Daily News 数据库维护")
    parser.add_argument("--dry-run", action="store_true", help="只统计数量，不修改数据库")
    parser.add_argument("--database-url", default=None, help="覆盖 DATABASE_URL（默认取 Settings）")
    parser.add_argument("--article-days", type=int, default=ARTICLE_RETENTION_DAYS)
    parser.add_argument("--event-days", type=int, default=EVENT_RETENTION_DAYS)
    parser.add_argument("--delivery-days", type=int, default=DELIVERY_RETENTION_DAYS)
    parser.add_argument("--ai-log-days", type=int, default=AI_LOG_RETENTION_DAYS)
    parser.add_argument("--no-compact-orphan-embeddings", action="store_true",
                        help="关闭孤立 Embedding 清理（默认开启；关闭后只做随行删除）")
    parser.add_argument("--vacuum", action="store_true",
                        help="清理后执行 VACUUM 主动回收空间")
    parser.add_argument("--json", action="store_true", help="额外输出 JSON 摘要")
    args = parser.parse_args(argv)

    from daily_news.config import Settings
    from daily_news.storage import Database

    db = Database(args.database_url or Settings().database_url)
    db.initialize()

    policy = MaintenancePolicy(
        article_retention_days=args.article_days,
        event_retention_days=args.event_days,
        delivery_retention_days=args.delivery_days,
        ai_log_retention_days=args.ai_log_days,
        compact_orphan_embeddings=not args.no_compact_orphan_embeddings,
    )
    report = biweekly_cleanup(db, dry_run=args.dry_run, policy=policy, vacuum=args.vacuum)
    print(format_report(report))
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI
    raise SystemExit(main())
