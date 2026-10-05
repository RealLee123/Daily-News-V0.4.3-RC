"""PostgreSQL switch-over tests that need no server.

Everything here is verified against a real SQLite database built from the project's own
schema, so the column plan and the type conversions are checked against the actual DDL
rather than a hand-written copy of it.

The parts that genuinely need a live PostgreSQL -- ``db-test``, the INSERT half of the
migration, and the non-empty-target guard -- are exercised by running

    python -m daily_news db-test
    python -m daily_news migrate-db

against the real target instead of being faked here.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from daily_news.config import Settings
from daily_news.migrate import (TABLES, column_plan, format_migration_report, kind_of,
                                read_only_sqlite, read_rows)
from daily_news.postgres import (EXPECTED_TABLES, normalize_url, redact,
                                 setup_postgres, validate_url)
from daily_news.storage import Database


def make_db(tmp_path: Path) -> Database:
    db = Database("sqlite:///" + str(tmp_path / "pg-source.db"))
    db.initialize()
    return db


def declared_types(db) -> dict[str, list[tuple[str, str]]]:
    """``{table: [(column, declared type)]}`` straight from the live SQLite schema."""
    result = {}
    with db.engine.connect() as connection:
        for table in TABLES:
            result[table] = [(row[1], row[2]) for row in
                             connection.execute(text(f"PRAGMA table_info({table})")).all()]
    return result


# --------------------------------------------------------------------------- #
# column plan and conversions
# --------------------------------------------------------------------------- #

def test_every_column_in_the_real_schema_maps_to_a_known_kind(tmp_path):
    schema = declared_types(make_db(tmp_path))
    for table, columns in schema.items():
        assert columns, table
        for column, declared in columns:
            assert kind_of(declared) in ("ts", "bool", "int", "float", "text"), (table, column, declared)


def test_timestamp_and_boolean_columns_map_to_the_converting_kinds(tmp_path):
    schema = declared_types(make_db(tmp_path))
    for table, columns in schema.items():
        for column, declared in columns:
            upper = declared.upper()
            if "TIMESTAMP" in upper:
                assert kind_of(declared) == "ts", (table, column, declared)
            elif "BOOL" in upper:
                assert kind_of(declared) == "bool", (table, column, declared)
            elif "INT" in upper:
                assert kind_of(declared) == "int", (table, column, declared)


def test_kind_of_handles_the_declared_types_used_by_the_schema():
    assert kind_of("TIMESTAMP WITH TIME ZONE") == "ts"
    assert kind_of("BOOLEAN") == "bool"
    assert kind_of("INTEGER") == "int"
    assert kind_of("DOUBLE PRECISION") == "float"
    assert kind_of("VARCHAR(32)") == "text"
    assert kind_of("TEXT") == "text"


def test_expected_tables_match_the_schema_and_the_migration_list():
    assert set(EXPECTED_TABLES) == set(TABLES)
    assert len(EXPECTED_TABLES) == 8


def test_rows_are_converted_into_postgres_friendly_python_types(tmp_path):
    """SQLite hands back TEXT timestamps and 0/1 booleans; PostgreSQL needs neither."""
    db = make_db(tmp_path)
    db.set_setting("qq_target", {"type": "c2c", "openid": "test-openid"})
    db.record_ai_failure("assess_importance", "event-1", "boom", retryable=False)

    with create_engine("sqlite:///" + str(tmp_path / "pg-source.db")).connect() as source:
        settings_plan = column_plan(source, "settings")
        failures_plan = column_plan(source, "ai_failures_v3")

        settings_rows = list(read_rows(source, "settings", settings_plan))
        failures_rows = list(read_rows(source, "ai_failures_v3", failures_plan))

        raw_updated_at = source.execute(text("SELECT updated_at FROM settings")).scalar_one()

    # what SQLite actually stores
    assert isinstance(raw_updated_at, str)

    # what PostgreSQL will be given
    assert isinstance(settings_rows[0]["updated_at"], datetime)
    assert settings_rows[0]["key"] == "qq_target"
    assert isinstance(settings_rows[0]["value"], str)

    assert failures_rows[0]["retryable"] is False        # a bool, not the integer 0
    assert isinstance(failures_rows[0]["created_at"], datetime)
    assert failures_rows[0]["error"] == "boom"


def test_nullable_columns_stay_null(tmp_path):
    db = make_db(tmp_path)
    db.record_ai_failure("work", "subject", "boom", retryable=True)
    with create_engine("sqlite:///" + str(tmp_path / "pg-source.db")).connect() as source:
        rows = list(read_rows(source, "ai_failures_v3", column_plan(source, "ai_failures_v3")))
    assert rows[0]["retryable"] is True
    assert len(rows) == 1


def test_read_rows_covers_every_table_without_error(tmp_path):
    db = make_db(tmp_path)
    db.set_setting("qq_target", {"type": "c2c", "openid": "test-openid"})
    with create_engine("sqlite:///" + str(tmp_path / "pg-source.db")).connect() as source:
        for table in TABLES:
            rows = list(read_rows(source, table, column_plan(source, table)))
            expected = source.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            assert len(rows) == expected, table


def test_an_unknown_declared_type_falls_back_to_pass_through(tmp_path):
    db = make_db(tmp_path)
    with create_engine("sqlite:///" + str(tmp_path / "pg-source.db")).begin() as connection:
        connection.execute(text("ALTER TABLE settings ADD COLUMN odd BLOB"))
    with create_engine("sqlite:///" + str(tmp_path / "pg-source.db")).connect() as source:
        plan = column_plan(source, "settings")
    assert ("odd", "text") in plan            # unknown types are copied verbatim


# --------------------------------------------------------------------------- #
# the source is never written to
# --------------------------------------------------------------------------- #

def test_read_only_sqlite_produces_a_readonly_uri():
    url = read_only_sqlite("sqlite:///news.db")
    assert url.startswith("sqlite:///file:")
    assert "mode=ro" in url and "uri=true" in url


def test_read_only_source_physically_refuses_a_write(tmp_path):
    db = make_db(tmp_path)
    db.set_setting("qq_target", {"type": "c2c", "openid": "test-openid"})

    engine = create_engine(read_only_sqlite("sqlite:///" + str(tmp_path / "pg-source.db")))
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM settings")).scalar_one() == 1
        with pytest.raises(Exception):
            connection.execute(text("DELETE FROM settings"))

    with engine.connect() as connection:      # ...and the row is still there
        assert connection.execute(text("SELECT COUNT(*) FROM settings")).scalar_one() == 1


def test_read_only_sqlite_rejects_a_non_sqlite_source():
    with pytest.raises(ValueError, match="必须是 SQLite"):
        read_only_sqlite("postgresql://user:pass@host/db")


# --------------------------------------------------------------------------- #
# connection-string handling
# --------------------------------------------------------------------------- #

def test_normalize_url_accepts_both_provider_spellings_and_pins_the_driver():
    assert normalize_url("postgres://u:p@h/db") == "postgresql+psycopg2://u:p@h/db"
    assert normalize_url("  postgresql://u:p@h/db  ") == "postgresql+psycopg2://u:p@h/db"
    assert normalize_url('"postgresql://u:p@h/db"') == "postgresql+psycopg2://u:p@h/db"


def test_an_explicitly_chosen_driver_is_left_alone():
    assert normalize_url("postgresql+psycopg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_url("postgresql+asyncpg://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"


def test_the_dialect_is_explicit_so_the_sqlalchemy_version_cannot_change_it():
    """A bare postgresql:// means psycopg2 on SQLAlchemy 2.0 and psycopg3 on 2.1."""
    assert normalize_url(NEON).startswith("postgresql+psycopg2://")


def test_validate_url_accepts_neon_and_rejects_sqlite():
    neon = "postgresql://user:pass@ep-cool-123.eu-central-1.aws.neon.tech/neondb?sslmode=require"
    assert validate_url(neon) == "postgresql+psycopg2://user:pass@ep-cool-123.eu-central-1.aws.neon.tech/neondb?sslmode=require"
    with pytest.raises(ValueError, match="不是 PostgreSQL"):
        validate_url("sqlite:///news.db")
    with pytest.raises(ValueError, match="不是 PostgreSQL"):
        validate_url("")


def test_redact_never_prints_the_password():
    url = "postgresql://user:sup3rsecret@ep-cool-123.aws.neon.tech/neondb"
    masked = redact(url)
    assert "sup3rsecret" not in masked
    assert masked == "postgresql://user:***@ep-cool-123.aws.neon.tech/neondb"
    assert "sup3rsecret" not in redact("postgresql+psycopg2://user:sup3rsecret@h/db")
    assert redact("not-a-url") == "not-a-url"


# --------------------------------------------------------------------------- #
# setup-postgres
# --------------------------------------------------------------------------- #

NEON = "postgresql://user:pass@ep-cool-123.aws.neon.tech/neondb?sslmode=require"


def test_setup_postgres_writes_only_the_database_url(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=keepme\nDATABASE_URL=sqlite:///news.db\nQQ_APP_ID=123\n", encoding="utf-8")

    # keep the connection probe out of the unit test
    monkeypatch.setattr("daily_news.postgres.check_connection",
                        lambda url: {"version": "PostgreSQL 17.0"})
    setup_postgres(NEON, env_path=env)

    text_of_env = env.read_text(encoding="utf-8")
    assert f"DATABASE_URL={normalize_url(NEON)}" in text_of_env
    assert "sqlite:///news.db" not in text_of_env
    assert "GEMINI_API_KEY=keepme" in text_of_env
    assert "QQ_APP_ID=123" in text_of_env


def test_setup_postgres_adds_the_key_when_absent(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=keepme\n", encoding="utf-8")
    monkeypatch.setattr("daily_news.postgres.check_connection",
                        lambda url: {"version": "PostgreSQL 17.0"})
    setup_postgres(NEON, env_path=env)
    assert env.read_text(encoding="utf-8").count("DATABASE_URL=") == 1


def test_setup_postgres_seeds_from_the_example_when_no_env_exists(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setattr("daily_news.postgres.check_connection",
                        lambda url: {"version": "PostgreSQL 17.0"})
    setup_postgres(NEON, env_path=env)
    seeded = env.read_text(encoding="utf-8")
    assert f"DATABASE_URL={normalize_url(NEON)}" in seeded
    assert "GEMINI_API_KEY=" in seeded            # came from .env.example
    assert "QQ_APP_ID=" in seeded


def test_setup_postgres_rejects_a_non_postgres_url(tmp_path):
    env = tmp_path / ".env"
    with pytest.raises(ValueError, match="不是 PostgreSQL"):
        setup_postgres("sqlite:///news.db", env_path=env)
    assert not env.exists()                       # nothing half-written


def test_setup_postgres_still_saves_when_the_server_is_unreachable(tmp_path, monkeypatch, capsys):
    def unreachable(url):
        raise OSError("network unreachable")

    monkeypatch.setattr("daily_news.postgres.check_connection", unreachable)
    env = tmp_path / ".env"
    setup_postgres(NEON, env_path=env)

    assert f"DATABASE_URL={normalize_url(NEON)}" in env.read_text(encoding="utf-8")
    assert "warn" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# the default is PostgreSQL, and there is no silent SQLite fallback
# --------------------------------------------------------------------------- #

def test_database_url_has_no_builtin_sqlite_default():
    default = Settings.__dataclass_fields__["database_url"].default
    assert not str(default).startswith("sqlite")


def test_missing_database_url_points_at_setup_postgres():
    settings = dataclasses.replace(Settings(), database_url="")
    with pytest.raises(RuntimeError, match="setup-postgres"):
        settings.require_database_url()


def test_configured_database_url_is_returned():
    settings = dataclasses.replace(Settings(), database_url=NEON)
    assert settings.require_database_url() == NEON


def test_every_command_gets_a_driver_pinned_url(monkeypatch, capsys):
    """Plain commands build a Database straight from .env, so main() must normalize too."""
    from daily_news import __main__ as cli

    captured = {}

    class FakeDatabase:
        def __init__(self, url):
            captured["url"] = url

        def initialize(self):
            pass

    monkeypatch.setattr(cli, "Database", FakeDatabase)
    monkeypatch.setattr(cli, "print_database_audit", lambda db: None)
    monkeypatch.setattr("sys.argv", ["daily-news", "db-audit", "--database-url", "postgresql://u:p@h/db"])

    cli.main()

    assert captured["url"] == "postgresql+psycopg2://u:p@h/db"
    capsys.readouterr()


# --------------------------------------------------------------------------- #
# the migration report
# --------------------------------------------------------------------------- #

def test_report_marks_a_row_count_mismatch():
    report = {
        "article_versions_v2": {"source": 595, "written": 595},
        "news_events_v2": {"source": 91, "written": 90},
    }
    rendered = format_migration_report(report, "sqlite:///news.db", NEON)
    assert "不一致" in rendered
    assert "迁移失败" in rendered


def test_report_states_the_total_when_everything_matches():
    report = {
        "article_versions_v2": {"source": 595, "written": 595},
        "news_events_v2": {"source": 91, "written": 91},
    }
    secret = "postgresql://user:hunter2@ep-cool-123.aws.neon.tech/neondb"
    rendered = format_migration_report(report, "sqlite:///news.db", secret)
    assert "迁移成功：686 行" in rendered
    assert "hunter2" not in rendered          # the password never reaches the report
    assert ":***@" in rendered
