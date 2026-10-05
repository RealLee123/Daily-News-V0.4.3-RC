"""One-off ``sqlite -> postgresql`` data migration.

The SQLite source is opened **read-only** and the target schema is created by
``Database.initialize()`` first, so the target needs no manual DDL and the news.db
backup can never be modified by this command.

Two things differ between the dialects and are converted by hand:

* **timestamps** -- SQLite stores them as TEXT (``2026-10-04 10:10:44.485509+00:00``),
  PostgreSQL wants a real ``timestamptz``;
* **booleans** -- SQLite stores 0/1 integers, PostgreSQL wants ``true``/``false``.

Both are read from the source's own ``PRAGMA table_info`` rather than reflected through
SQLAlchemy: reflection maps ``TIMESTAMP WITH TIME ZONE`` to ``NUMERIC`` on SQLite and
then raises ``TypeError: must be real number, not str`` on the first row. The column
plan therefore comes from the live source, so it cannot drift from the schema either.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, text

from daily_news.postgres import redact, validate_url
from daily_news.storage import Database

# The order the migration reports in; there are no foreign keys, so insertion order is
# free, but keeping it stable makes the output diffable between runs.
TABLES = (
    "article_versions_v2",
    "news_events_v2",
    "event_articles_v2",
    "digest_event_history_v3",
    "deliveries",
    "ai_decisions_v2",
    "ai_failures_v3",
    "settings",
)

def kind_of(declared: str) -> str:
    """Map a declared column type onto the conversion it needs."""
    upper = (declared or "").upper()
    if "TIMESTAMP" in upper or "DATE" in upper:
        return "ts"
    if "BOOL" in upper:
        return "bool"
    if "INT" in upper:
        return "int"
    if any(token in upper for token in ("DOUBLE", "REAL", "FLOAT", "NUMERIC")):
        return "float"
    return "text"


def convert(value, kind: str, where: str):
    """Coerce one value into what PostgreSQL expects, or explain which cell failed."""
    if value is None:
        return None
    if kind == "ts":
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except ValueError as exc:
            raise ValueError(f"{where}：时间戳无法解析 {value!r}") from exc
    if kind == "bool":
        return bool(int(value))
    if kind == "int":
        return int(value)
    if kind == "float":
        return float(value)
    return value


def column_plan(connection, table: str) -> list[tuple[str, str]]:
    """``[(column, kind)]`` in declaration order, taken from the source itself."""
    rows = connection.execute(text(f"PRAGMA table_info({table})")).all()
    if not rows:
        raise RuntimeError(f"源库里没有表 {table}")
    return [(row[1], kind_of(row[2])) for row in rows]


def read_rows(connection, table: str, plan: list[tuple[str, str]]):
    """Stream converted rows out of the SQLite source."""
    columns = [name for name, _ in plan]
    statement = "SELECT %s FROM %s" % (", ".join(columns), table)
    for row in connection.execute(text(statement)).mappings():
        yield {name: convert(row[name], kind, f"{table}.{name}") for name, kind in plan}


def read_only_sqlite(url: str) -> str:
    """Turn ``sqlite:///news.db`` into a URL that physically cannot write."""
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError("migrate-db 的源必须是 SQLite，例如 sqlite:///news.db")
    path = url[len(prefix):]
    if not path or path.startswith("file:"):
        raise ValueError("无法解析 SQLite 源路径：" + url)
    resolved = Path(path).resolve().as_posix()
    return f"sqlite:///file:{resolved}?mode=ro&uri=true"


def migrate(source_url: str, target_url: str, force: bool = False) -> dict:
    """Copy every table across, verifying each row count on the way out."""
    target_url = validate_url(target_url)
    source_engine = create_engine(read_only_sqlite(source_url))
    target_engine = create_engine(target_url, pool_pre_ping=True)
    report: dict[str, dict[str, int]] = {}

    try:
        Database(target_url).initialize()                   # idempotent: CREATE IF NOT EXISTS
        with source_engine.connect() as source:
            plans = {table: column_plan(source, table) for table in TABLES}
            source_counts = {table: int(source.execute(
                text(f"SELECT COUNT(*) FROM {table}")).scalar_one()) for table in TABLES}

            with target_engine.begin() as target:
                existing = {table: int(target.execute(
                    text(f"SELECT COUNT(*) FROM {table}")).scalar_one()) for table in TABLES}
                occupied = {table: count for table, count in existing.items() if count}
                if occupied and not force:
                    raise RuntimeError(
                        "目标库已有数据，未做任何改动："
                        + ", ".join(f"{table}={count}" for table, count in sorted(occupied.items()))
                        + "。确认要覆盖请加 --force（会先清空这些表）。"
                    )
                if occupied:
                    for table in TABLES:
                        target.execute(text(f"DELETE FROM {table}"))

                for table in TABLES:
                    plan = plans[table]
                    columns = [name for name, _ in plan]
                    rows = list(read_rows(source, table, plan))
                    if rows:
                        insert = "INSERT INTO %s (%s) VALUES (%s)" % (
                            table, ", ".join(columns), ", ".join(":" + name for name in columns))
                        target.execute(text(insert), rows)
                    written = int(target.execute(
                        text(f"SELECT COUNT(*) FROM {table}")).scalar_one())
                    report[table] = {"source": source_counts[table], "written": written}
    finally:
        source_engine.dispose()
        target_engine.dispose()
    return report


def format_migration_report(report: dict, source_url: str, target_url: str) -> str:
    lines = [f"源（只读）：{source_url}", f"目标：{redact(target_url)}", ""]
    lines.append("%-26s %10s %10s %s" % ("表", "源行数", "写入", "结果"))
    lines.append("-" * 60)
    total_source = total_written = 0
    failures = []
    for table, counts in report.items():
        total_source += counts["source"]
        total_written += counts["written"]
        ok = counts["source"] == counts["written"]
        if not ok:
            failures.append(table)
        lines.append("%-26s %10d %10d %s" % (
            table, counts["source"], counts["written"], "OK" if ok else "不一致"))
    lines.append("-" * 60)
    lines.append("%-26s %10d %10d" % ("合计", total_source, total_written))
    lines.append("")
    if failures:
        lines.append("迁移失败：以下表行数不一致 -> " + ", ".join(failures))
    else:
        lines.append(f"迁移成功：{total_written} 行，全部 {len(report)} 张表行数一致。")
    return "\n".join(lines)
