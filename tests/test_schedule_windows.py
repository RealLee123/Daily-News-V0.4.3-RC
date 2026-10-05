"""Run-scheduling tests: edition windows, the breaking-alert queue, job separation.

The headline regression here is the timezone basis. Stored timestamps are UTC strings
(``2026-10-04 10:10:44+00:00``) and SQLite compares them as text, so a window boundary
built with ``ZoneInfo("Asia/Shanghai")`` carries a ``+08:00`` suffix that sorts eight
hours later than every stored row. A three-hour breaking lookback built that way
matched nothing at all.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import text

from daily_news.audit import database_snapshot
from daily_news.jobs import _as_db_time, _edition_window, pending_breaking_alerts
from daily_news.maintenance import biweekly_cleanup
from daily_news.storage import Database

CST = ZoneInfo("Asia/Shanghai")

EVENT_INSERT = """
INSERT INTO news_events_v2
    (id, canonical_title, summary, category, region, first_seen, last_seen, embedding,
     article_count, independent_source_count, official_source_count, velocity_30m,
     importance, breaking, scope, confidence, importance_reason, status, assessed_at,
     assessment_status, assessment_attempts, last_assessment_error)
VALUES
    (:id, :title, :summary, 'world', 'Global', :at, :at, :embedding,
     :sources, :sources, :officials, 0,
     :importance, :breaking, 'global', :confidence, 'reason', :status, :at,
     :assessment_status, 1, NULL)
"""


def make_db(tmp_path) -> Database:
    db = Database("sqlite:///" + str(tmp_path / "schedule.db"))
    db.initialize()
    return db


def make_settings(**overrides) -> SimpleNamespace:
    base = dict(
        timezone="Asia/Shanghai", morning_time="09:30", evening_time="21:30",
        breaking_threshold=80, breaking_min_sources=2, breaking_min_confidence=0.80,
        breaking_pool_hours=6, breaking_max_alerts_per_run=3,
        qq_message_max_chars=1800, max_digest_events=24,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def add_event(db, event_id, *, at=None, hours_old=0.0, breaking=True, importance=95,
              confidence=0.95, sources=3, officials=1, status="confirmed",
              assessment_status="done") -> str:
    # Production always writes UTC (the collectors astimezone to UTC and Article fills
    # in UTC for naive input), so normalize here too -- otherwise this helper would
    # create a +08:00 row that the real code never produces.
    at = at if at is not None else datetime.now(timezone.utc) - timedelta(hours=hours_old)
    at = _as_db_time(at)
    with db.engine.begin() as connection:
        connection.execute(text(EVENT_INSERT), {
            "id": event_id, "title": "Event " + event_id, "summary": "Summary", "at": at,
            "embedding": json.dumps([1.0, 0.0]), "sources": sources, "officials": officials,
            "importance": importance, "breaking": breaking, "confidence": confidence,
            "status": status, "assessment_status": assessment_status,
        })
    return event_id


def mark_alerted(db, event_id) -> None:
    with db.engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO deliveries (id, kind, event_fingerprint, delivered_at, target_type, target_openid)
            VALUES (:id, 'breaking', :event_id, :at, 'c2c', 'placeholder')
        """), {"id": "breaking:" + event_id, "event_id": event_id,
               "at": datetime.now(timezone.utc)})


# --------------------------------------------------------------------------- #
# timezone basis -- the regression
# --------------------------------------------------------------------------- #

def test_local_timezone_boundary_misses_recent_rows_but_utc_finds_them(tmp_path):
    """A +08:00 boundary sorts eight hours late against UTC-stored text."""
    db = make_db(tmp_path)
    add_event(db, "recent", hours_old=1)

    local_cutoff = datetime.now(CST) - timedelta(hours=3)
    utc_cutoff = _as_db_time(local_cutoff)

    assert len(db.breaking_event_pool(local_cutoff)) == 0
    assert [event["id"] for event in db.breaking_event_pool(utc_cutoff)] == ["recent"]


def test_as_db_time_keeps_the_instant_and_changes_only_the_representation():
    moment = datetime(2026, 3, 10, 9, 30, tzinfo=CST)
    converted = _as_db_time(moment)
    assert converted == moment                       # same instant
    assert converted.tzinfo is timezone.utc
    assert converted.hour == 1 and converted.day == 10


def test_edition_window_picks_the_intended_local_hours(tmp_path):
    settings = make_settings()
    db = make_db(tmp_path)
    local_now = datetime(2026, 3, 10, 10, 0, tzinfo=CST)

    start, end = _edition_window(settings, "morning", now=local_now)
    assert (start.day, start.hour, start.minute) == (9, 21, 30)
    assert (end.day, end.hour, end.minute) == (10, 9, 30)

    add_event(db, "inside", at=datetime(2026, 3, 10, 8, 0, tzinfo=CST))
    add_event(db, "outside", at=datetime(2026, 3, 10, 12, 0, tzinfo=CST))

    found = [event["id"] for event in db.events_between(_as_db_time(start), _as_db_time(end), 100)]
    assert found == ["inside"]


def test_edition_windows_tile_the_day_without_gaps_or_overlap():
    settings = make_settings()
    local_now = datetime(2026, 3, 10, 10, 0, tzinfo=CST)
    morning_start, morning_end = _edition_window(settings, "morning", now=local_now)
    evening_start, evening_end = _edition_window(settings, "evening", now=local_now)

    assert morning_end == evening_start
    assert morning_end - morning_start == timedelta(hours=12)
    assert evening_end - evening_start == timedelta(hours=12)


# --------------------------------------------------------------------------- #
# breaking alert queue
# --------------------------------------------------------------------------- #

def test_a_qualifying_event_is_offered(tmp_path):
    db = make_db(tmp_path)
    add_event(db, "crash", hours_old=1)
    assert [event["id"] for event in pending_breaking_alerts(make_settings(), db)] == ["crash"]


def test_events_below_the_reliability_gate_are_not_offered(tmp_path):
    db = make_db(tmp_path)
    add_event(db, "low-importance", hours_old=1, importance=70)
    add_event(db, "low-confidence", hours_old=1, confidence=0.5)
    add_event(db, "single-unofficial-source", hours_old=1, sources=1, officials=0)
    add_event(db, "not-flagged-breaking", hours_old=1, breaking=False)
    add_event(db, "background", hours_old=1, status="background")
    assert pending_breaking_alerts(make_settings(), db) == []


def test_an_event_with_one_official_source_passes(tmp_path):
    db = make_db(tmp_path)
    add_event(db, "official-only", hours_old=1, sources=1, officials=1)
    assert [event["id"] for event in pending_breaking_alerts(make_settings(), db)] == ["official-only"]


def test_already_delivered_events_are_not_offered_again(tmp_path):
    db = make_db(tmp_path)
    add_event(db, "already-sent", hours_old=1)
    assert len(pending_breaking_alerts(make_settings(), db)) == 1

    mark_alerted(db, "already-sent")

    assert pending_breaking_alerts(make_settings(), db) == []


def test_alerts_are_capped_per_run_with_the_most_important_first(tmp_path):
    db = make_db(tmp_path)
    add_event(db, "third", hours_old=1, importance=82)
    add_event(db, "first", hours_old=1, importance=99)
    add_event(db, "second", hours_old=1, importance=90)

    offered = pending_breaking_alerts(make_settings(breaking_max_alerts_per_run=2), db)

    assert [event["id"] for event in offered] == ["first", "second"]


def test_window_outlives_a_two_hour_scan_so_nothing_is_stranded(tmp_path):
    """An event that has waited through several runs is still offered."""
    db = make_db(tmp_path)
    add_event(db, "waited", hours_old=5)
    assert [event["id"] for event in pending_breaking_alerts(make_settings(), db)] == ["waited"]


def test_events_outside_the_window_are_ignored(tmp_path):
    db = make_db(tmp_path)
    add_event(db, "old", hours_old=8)
    assert pending_breaking_alerts(make_settings(), db) == []


# --------------------------------------------------------------------------- #
# job separation: the scan processes, the edition only displays
# --------------------------------------------------------------------------- #

class FakeAI:
    model_name = "fake-model"

    def edit_digest(self, kind, events):
        return "## 测试简报\n\n正文"

    def assess_substantive_update(self, previous, current):
        return {"substantive": True, "update_label": "有进展"}


class FakeBot:
    """Records what each push path was asked to send."""

    instances: list["FakeBot"] = []

    def __init__(self, settings, db):
        self.markdown_batches = []
        self.plain_batches = []
        FakeBot.instances.append(self)

    async def push_many_markdown_while_online(self, messages):
        self.markdown_batches.append(list(messages))

    async def push_many_while_online(self, messages):
        self.plain_batches.append(list(messages))


def edition_covering_now() -> str:
    """The edition whose window the current local time falls into."""
    current = datetime.now(CST).time()
    return "evening" if time(9, 30) <= current < time(21, 30) else "morning"


def test_digest_job_reads_events_without_collecting(monkeypatch, tmp_path):
    """The edition must not re-fetch RSS, re-embed or re-cluster."""
    from daily_news import jobs

    db = make_db(tmp_path)
    db.set_setting("qq_target", {"type": "c2c", "openid": "test-openid"})
    settings = make_settings(qq_message_max_chars=1800)
    kind = edition_covering_now()
    start, _ = _edition_window(settings, kind)
    add_event(db, "already-processed", at=start + timedelta(minutes=5))

    async def forbidden(*args, **kwargs):
        raise AssertionError("digest_job must not collect: the scan already processed this window")

    FakeBot.instances.clear()
    monkeypatch.setattr(jobs, "collect", forbidden)
    monkeypatch.setattr(jobs, "create_ai_provider", lambda settings: FakeAI())
    monkeypatch.setattr(jobs, "QQBot", FakeBot)
    monkeypatch.setattr(jobs, "split_qq_markdown", lambda text, limit: ["chunk-1"])
    settings.require_ai = lambda: None

    asyncio.run(jobs.digest_job(settings, db, kind))

    assert FakeBot.instances[-1].markdown_batches == [["chunk-1"]]
    with db.engine.connect() as connection:
        delivered = connection.execute(text("SELECT id, kind FROM deliveries")).all()
    assert [(row[0], row[1]) for row in delivered] == [(f"{kind}:{start.date().isoformat()}", kind)]


def test_digest_job_sends_nothing_when_the_edition_was_already_pushed(monkeypatch, tmp_path):
    from daily_news import jobs

    db = make_db(tmp_path)
    db.set_setting("qq_target", {"type": "c2c", "openid": "test-openid"})
    settings = make_settings(qq_message_max_chars=1800)
    kind = edition_covering_now()
    start, _ = _edition_window(settings, kind)
    add_event(db, "already-processed", at=start + timedelta(minutes=5))
    db.mark_delivered(f"{kind}:{start.date().isoformat()}", kind, {"type": "c2c", "openid": "test-openid"})

    async def forbidden(*args, **kwargs):
        raise AssertionError("digest_job must not collect")

    FakeBot.instances.clear()
    monkeypatch.setattr(jobs, "collect", forbidden)
    monkeypatch.setattr(jobs, "create_ai_provider", lambda settings: FakeAI())
    monkeypatch.setattr(jobs, "QQBot", FakeBot)
    settings.require_ai = lambda: None

    asyncio.run(jobs.digest_job(settings, db, kind))

    assert FakeBot.instances == []      # returns before even building a bot


def test_digest_job_first_run_writes_the_delivery_and_the_history(monkeypatch, tmp_path):
    """First run of an edition: one delivery row, plus one history row per event sent."""
    from daily_news import jobs

    db = make_db(tmp_path)
    db.set_setting("qq_target", {"type": "c2c", "openid": "test-openid"})
    settings = make_settings(qq_message_max_chars=1800)
    kind = edition_covering_now()
    start, _ = _edition_window(settings, kind)
    add_event(db, "fresh", at=start + timedelta(minutes=5))

    FakeBot.instances.clear()
    monkeypatch.setattr(jobs, "create_ai_provider", lambda settings: FakeAI())
    monkeypatch.setattr(jobs, "QQBot", FakeBot)
    monkeypatch.setattr(jobs, "split_qq_markdown", lambda text, limit: ["chunk-1"])
    settings.require_ai = lambda: None

    asyncio.run(jobs.digest_job(settings, db, kind))

    assert FakeBot.instances[-1].markdown_batches == [["chunk-1"]]
    snapshot = database_snapshot(db)
    assert snapshot["deliveries"] == 1
    assert snapshot["digest_history"] == 1


def test_digest_job_repeat_is_a_quiet_no_op(monkeypatch, tmp_path, capsys):
    """The real production sequence: delivery AND digest history both already written.

    Regression: with the idempotency check sitting *after* event selection, the repeat
    found every event already digested, ended up with zero candidates and died on the
    "no new events" error -- no duplicate push, but a traceback and a non-zero exit code
    in the scheduler's log. The check now runs first, so a repeat is silent and exits 0.
    """
    from daily_news import jobs

    db = make_db(tmp_path)
    db.set_setting("qq_target", {"type": "c2c", "openid": "test-openid"})
    settings = make_settings(qq_message_max_chars=1800)
    kind = edition_covering_now()
    start, _ = _edition_window(settings, kind)
    event_id = "already-digested"
    add_event(db, event_id, at=start + timedelta(minutes=5))

    delivery_id = f"{kind}:{start.date().isoformat()}"
    db.mark_delivered(delivery_id, kind, {"type": "c2c", "openid": "test-openid"})
    db.record_digest_events(delivery_id, kind, [db.event_payload(event_id)])
    before = database_snapshot(db)

    def forbidden(*args, **kwargs):
        raise AssertionError("a repeat must not even select events")

    FakeBot.instances.clear()
    monkeypatch.setattr(jobs, "prepare_digest_events", forbidden)
    monkeypatch.setattr(jobs, "create_ai_provider", lambda settings: FakeAI())
    monkeypatch.setattr(jobs, "QQBot", FakeBot)
    settings.require_ai = lambda: None

    asyncio.run(jobs.digest_job(settings, db, kind))        # must not raise

    assert "本期已经推送，跳过重复发送" in capsys.readouterr().out
    assert FakeBot.instances == []
    assert database_snapshot(db) == before


def test_news_scan_processes_and_alerts_without_building_a_digest(monkeypatch, tmp_path):
    from daily_news import jobs

    db = make_db(tmp_path)
    db.set_setting("qq_target", {"type": "c2c", "openid": "test-openid"})
    add_event(db, "crash", hours_old=1)

    async def fake_collect(settings, db, ai=None):
        return {"new_articles": 1}

    FakeBot.instances.clear()
    monkeypatch.setattr(jobs, "collect", fake_collect)
    monkeypatch.setattr(jobs, "create_ai_provider", lambda settings: FakeAI())
    monkeypatch.setattr(jobs, "split_qq_markdown", lambda text, limit: ["alert-1"])
    monkeypatch.setattr(jobs, "QQBot", FakeBot)
    settings = make_settings()
    settings.require_ai = lambda: None

    asyncio.run(jobs.breaking_job(settings, db))

    assert FakeBot.instances[-1].markdown_batches == [["alert-1"]]
    with db.engine.connect() as connection:
        kinds = [row[0] for row in connection.execute(text("SELECT kind FROM deliveries"))]
    assert kinds == ["breaking"]
    # a scan must not touch any digest state either
    assert database_snapshot(db)["digest_history"] == 0


def test_the_0820_and_2020_scans_feed_their_own_edition(tmp_path):
    """08:20 lands in the morning window, 20:20 in the evening one, and neither leaks."""
    settings = make_settings()
    db = make_db(tmp_path)
    midday = datetime(2026, 3, 10, 12, 0, tzinfo=CST)
    morning_start, morning_end = _edition_window(settings, "morning", now=midday)
    evening_start, evening_end = _edition_window(settings, "evening", now=midday)

    add_event(db, "scan-0820", at=datetime(2026, 3, 10, 8, 20, tzinfo=CST))
    add_event(db, "scan-2020", at=datetime(2026, 3, 10, 20, 20, tzinfo=CST))

    in_morning = [event["id"] for event in
                  db.events_between(_as_db_time(morning_start), _as_db_time(morning_end), 100)]
    in_evening = [event["id"] for event in
                  db.events_between(_as_db_time(evening_start), _as_db_time(evening_end), 100)]

    assert in_morning == ["scan-0820"]
    assert in_evening == ["scan-2020"]


def test_maintenance_and_the_scan_leave_each_other_working(tmp_path):
    """The retention sweep and the two-hourly scan have to share one database."""
    db = make_db(tmp_path)
    settings = make_settings()
    add_event(db, "fresh", hours_old=1)
    add_event(db, "stale", hours_old=24 * 40)

    report = biweekly_cleanup(db)

    assert report.stage("events").affected == 1                    # only the stale event goes
    assert [event["id"] for event in pending_breaking_alerts(settings, db)] == ["fresh"]
    snapshot = database_snapshot(db)
    assert snapshot["events"] == 1
    assert snapshot["deliveries"] == 0                             # never fabricates a delivery
    assert snapshot["pending_events"] == 0                         # deletes, never re-queues for AI


def test_news_scan_is_wired_to_the_scan_job(monkeypatch):
    from daily_news import __main__ as cli

    called = []

    async def fake_job(settings, db):
        called.append("scan")

    monkeypatch.setattr(cli, "breaking_job", fake_job)
    args = SimpleNamespace(command="news-scan", dry_run=False, vacuum=False)
    settings = SimpleNamespace(require_qq=lambda: None)
    cli._run_command(args, settings, db="db-handle")
    assert called == ["scan"]


def test_breaking_remains_an_alias_for_the_same_scan(monkeypatch):
    from daily_news import __main__ as cli

    called = []

    async def fake_job(settings, db):
        called.append("scan")

    monkeypatch.setattr(cli, "breaking_job", fake_job)
    args = SimpleNamespace(command="breaking", dry_run=False, vacuum=False)
    settings = SimpleNamespace(require_qq=lambda: None)
    cli._run_command(args, settings, db="db-handle")
    assert called == ["scan"]


def test_editions_dispatch_with_their_own_kind(monkeypatch):
    from daily_news import __main__ as cli

    seen = []

    async def fake_digest(settings, db, kind):
        seen.append(kind)

    monkeypatch.setattr(cli, "digest_job", fake_digest)
    settings = SimpleNamespace(require_qq=lambda: None)
    for kind in ("morning", "evening"):
        cli._run_command(SimpleNamespace(command=kind, dry_run=False, vacuum=False), settings, db="db-handle")
    assert seen == ["morning", "evening"]
