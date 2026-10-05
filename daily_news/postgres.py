"""PostgreSQL setup and connectivity.

PostgreSQL is the production database; SQLite is kept only as the local backup and as
the source for the one-off migration in :mod:`daily_news.migrate`. Nothing here touches
collection, embedding, clustering, breaking or delivery logic.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import create_engine, text

from daily_news.config import ROOT
from daily_news.storage import Database
from daily_news.storage.database import SCHEMA

# Read straight out of the real DDL so this can never drift from the schema.
EXPECTED_TABLES = tuple(re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", " ".join(SCHEMA)))

# Neon prints ``postgresql://...?sslmode=require``; plain ``postgres://`` is the older
# Heroku spelling and SQLAlchemy 2.x refuses it outright.
POSTGRES_PREFIX = "postgresql://"

# A bare ``postgresql://`` picks a different driver depending on the SQLAlchemy version
# (2.0 -> psycopg2, 2.1 -> psycopg3), which shows up as a bare ModuleNotFoundError on a
# machine that has the other one installed. Pin the driver in the URL so the behaviour
# is the same everywhere. An explicitly chosen driver is left alone.
DRIVER_SUFFIX = "+psycopg2"


def normalize_url(url: str) -> str:
    """Accept the spellings providers hand out and settle on a driver-pinned URL."""
    url = (url or "").strip().strip('"').strip("'")
    if url.startswith("postgres://"):
        url = POSTGRES_PREFIX + url[len("postgres://"):]
    if url.startswith(POSTGRES_PREFIX):
        return "postgresql" + DRIVER_SUFFIX + "://" + url[len(POSTGRES_PREFIX):]
    return url


def validate_url(url: str) -> str:
    """Return the normalized URL, or refuse anything that is not PostgreSQL."""
    normalized = normalize_url(url)
    if not normalized.startswith("postgresql"):
        raise ValueError(
            "不是 PostgreSQL 连接串（应以 postgresql:// 开头）：" + (normalized[:40] or "(空)")
        )
    return normalized


def redact(url: str) -> str:
    """Show enough to identify the target without ever printing the password."""
    match = re.match(r"(postgresql(?:\+\w+)?://)([^:/@]+):([^@]+)@(.+)", url)
    if not match:
        return url
    scheme, user, _, rest = match.groups()
    return f"{scheme}{user}:***@{rest}"


def upsert_env_value(env_path: Path, key: str, value: str) -> None:
    """Set ``key=value`` in a .env, leaving every other line exactly as it was."""
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    output, replaced = [], False
    for line in lines:
        if line.strip().startswith(f"{key}="):
            output.append(f"{key}={value}")
            replaced = True
        else:
            output.append(line)
    if not replaced:
        output.append(f"{key}={value}")
    env_path.write_text("\n".join(output) + "\n", encoding="utf-8")


def setup_postgres(url: str | None = None, env_path: Path | None = None) -> str:
    """Save a PostgreSQL URL into .env, seeding the file from .env.example if needed.

    Saving and connecting are deliberately separate: the URL is written first, and a
    failed connection is reported as a warning so this works the same offline.
    """
    env_path = Path(env_path) if env_path else ROOT / ".env"

    if url is None:
        print("粘贴 Neon 的 PostgreSQL 连接串，形如：")
        print("  postgresql://user:password@ep-xxx-xxx.region.aws.neon.tech/neondb?sslmode=require")
        url = input("DATABASE_URL: ")

    # Validate before touching the filesystem: a rejected URL must leave nothing behind.
    url = validate_url(url)

    if not env_path.exists():
        example = ROOT / ".env.example"
        if example.exists():
            env_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")

    upsert_env_value(env_path, "DATABASE_URL", url)
    print(f"已写入 {env_path}")
    print(f"  DATABASE_URL={redact(url)}")

    try:
        info = check_connection(url)
        print(f"连接成功：{info['version']}")
        print("下一步：python -m daily_news db-test")
    except Exception as exc:                                   # noqa: BLE001 - report, never hide
        print(f"[warn] 现在连不上（配置已保存，可稍后重试 db-test）：{type(exc).__name__}: {exc}")
    return url


def check_connection(url: str) -> dict:
    """One round trip: prove the credentials work and say what we are talking to."""
    engine = create_engine(validate_url(url), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            version = connection.execute(text("SELECT version()")).scalar_one()
        return {"version": str(version).split(" on ")[0]}
    finally:
        engine.dispose()


def table_report(url: str) -> dict:
    """Row counts per expected table, and which expected tables are missing."""
    engine = create_engine(validate_url(url), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            present = set(connection.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public'")).scalars())
            counts = {}
            for table in EXPECTED_TABLES:
                if table in present:
                    counts[table] = int(connection.execute(
                        text(f"SELECT COUNT(*) FROM {table}")).scalar_one())
        return {"tables": counts, "missing": sorted(set(EXPECTED_TABLES) - present)}
    finally:
        engine.dispose()


def db_test(url: str) -> int:
    """The acceptance check for a fresh PostgreSQL: connect, create, count."""
    url = validate_url(url)
    info = check_connection(url)
    print(f"目标：{redact(url)}")
    print(f"服务器：{info['version']}")

    Database(url).initialize()                              # CREATE TABLE IF NOT EXISTS
    print("schema：Database.initialize() 已执行（幂等）")

    report = table_report(url)
    present = report["tables"]
    print(f"表数量：{len(present)}/{len(EXPECTED_TABLES)}")
    for table in EXPECTED_TABLES:
        count = present.get(table)
        mark = "OK " if table in present else "缺失"
        print(f"  {mark} {table:<26} {'' if count is None else count}")

    if report["missing"]:
        print("失败：以下表不存在：" + ", ".join(report["missing"]))
        return 1
    print("db-test 通过。")
    return 0
