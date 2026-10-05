"""Maintenance tests.

Every row is written with the real column list from
``daily_news.storage.database.SCHEMA`` -- nothing here is mocked and no column is
guessed. Rows are inserted relative to ``NOW`` so the retention windows are
deterministic.

Policy under test::

    Article   14 days and not referenced by an Event (is_latest plays no part)
    Event     28 days (its event_articles_v2 rows go with it)
    Embedding deleted with its owner; unreachable vectors are cleared by default;
              no surviving embedding is ever rewritten or refreshed
    AI logs   90 days
    Delivery  30 days
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from daily_news.audit import database_snapshot
from daily_news.maintenance import MaintenancePolicy, biweekly_cleanup
from daily_news.storage import Database

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)

ARTICLE_INSERT = """
INSERT INTO article_versions_v2
    (id, source_id, source_name, source_group, source_kind, official, source_tier, primary_source,
     default_weight, external_id, original_url, canonical_url, content_hash, family_key,
     title, summary, category, region, published_at, discovered_at, version_no, is_latest,
     cluster_status, embedding, embedding_model, embedding_dimensions, embedded_at)
VALUES
    (:id, :source_id, :source_name, :source_group, :source_kind, :official, :source_tier, :primary_source,
     :default_weight, :external_id, :original_url, :canonical_url, :content_hash, :family_key,
     :title, :summary, :category, :region, :published_at, :discovered_at, :version_no, :is_latest,
     :cluster_status, :embedding, :embedding_model, :embedding_dimensions, :embedded_at)
"""

EVENT_INSERT = """
INSERT INTO news_events_v2
    (id, canonical_title, summary, category, region, first_seen, last_seen, embedding,
     article_count, independent_source_count, official_source_count, velocity_30m,
     importance, breaking, scope, confidence, importance_reason, status, assessed_at,
     assessment_status, assessment_attempts, last_assessment_error)
VALUES
    (:id, :title, :summary, 'world', 'Global', :first_seen, :last_seen, :embedding,
     0, 0, 0, 0,
     :importance, :breaking, 'global', 0.9, 'reason', 'confirmed', :first_seen,
     'done', 1, NULL)
"""


def make_db(tmp_path) -> Database:
    db = Database("sqlite:///" + str(tmp_path / "maintenance.db"))
    db.initialize()
    return db


def add_article(db, article_id, *, days_old=0, is_latest=True, version_no=1,
                cluster_status="clustered", embedding=None, family_key="fam") -> str:
    published = NOW - timedelta(days=days_old)
    with db.engine.begin() as connection:
        connection.execute(text(ARTICLE_INSERT), {
            "id": article_id, "source_id": "src", "source_name": "Source",
            "source_group": "group", "source_kind": "news_media", "official": False,
            "source_tier": "high", "primary_source": False, "default_weight": 1.0,
            "external_id": article_id, "original_url": "https://example.com/" + article_id,
            "canonical_url": "https://example.com/" + article_id,
            "content_hash": (article_id + "0" * 64)[:64], "family_key": family_key,
            "title": "Title " + article_id, "summary": "Summary", "category": "world",
            "region": "Global", "published_at": published, "discovered_at": published,
            "version_no": version_no, "is_latest": is_latest, "cluster_status": cluster_status,
            "embedding": json.dumps(embedding) if embedding is not None else None,
            "embedding_model": "test-embedding" if embedding is not None else None,
            "embedding_dimensions": len(embedding) if embedding is not None else None,
            "embedded_at": published if embedding is not None else None,
        })
    return article_id


def add_event(db, event_id, *, days_old=0, breaking=False, importance=50) -> str:
    last_seen = NOW - timedelta(days=days_old)
    with db.engine.begin() as connection:
        connection.execute(text(EVENT_INSERT), {
            "id": event_id, "title": "Event " + event_id, "summary": "Summary",
            "first_seen": last_seen, "last_seen": last_seen, "embedding": json.dumps([1.0, 0.0]),
            "importance": importance, "breaking": breaking,
        })
    return event_id


def link(db, event_id, article_id) -> None:
    with db.engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO event_articles_v2 (event_id, article_id, linked_at, link_method, link_confidence)
            VALUES (:event_id, :article_id, :at, 'embedding_auto', 0.95)
        """), {"event_id": event_id, "article_id": article_id, "at": NOW})


def add_ai_decision(db, row_id, *, days_old) -> None:
    with db.engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO ai_decisions_v2
                (id, decision_type, subject_id, input_json, output_json, model, created_at)
            VALUES (:id, 'importance', 'subject', '{}', '{}', 'model', :at)
        """), {"id": row_id, "at": NOW - timedelta(days=days_old)})


def add_ai_failure(db, row_id, *, days_old) -> None:
    with db.engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO ai_failures_v3 (id, work_type, subject_id, error, retryable, created_at)
            VALUES (:id, 'embedding', 'subject', 'error', 1, :at)
        """), {"id": row_id, "at": NOW - timedelta(days=days_old)})


def add_delivery(db, delivery_id, *, days_old) -> None:
    with db.engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO deliveries (id, kind, event_fingerprint, delivered_at, target_type, target_openid)
            VALUES (:id, 'breaking', NULL, :at, 'c2c', 'placeholder')
        """), {"id": delivery_id, "at": NOW - timedelta(days=days_old)})


def add_digest_row(db, edition_id, event_id, *, days_old) -> None:
    with db.engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO digest_event_history_v3
                (edition_id, kind, event_id, snapshot_json, included_at, substantive_update)
            VALUES (:edition_id, 'morning', :event_id, '{}', :at, 0)
        """), {"edition_id": edition_id, "event_id": event_id, "at": NOW - timedelta(days=days_old)})


def count_rows(db, table) -> int:
    with db.engine.connect() as connection:
        return int(connection.execute(text("SELECT COUNT(*) FROM " + table)).scalar_one())


def embedding_of(db, article_id):
    with db.engine.connect() as connection:
        return connection.execute(
            text("SELECT embedding, embedding_model, embedding_dimensions, embedded_at "
                 "FROM article_versions_v2 WHERE id = :id"), {"id": article_id}
        ).first()


def embedding_state(db) -> dict:
    """Every embedding column of every surviving article row, for byte comparison."""
    with db.engine.connect() as connection:
        return {
            row[0]: (row[1], row[2], row[3], None if row[4] is None else str(row[4]))
            for row in connection.execute(text(
                "SELECT id, embedding, embedding_model, embedding_dimensions, embedded_at "
                "FROM article_versions_v2"
            ))
        }


def event_embedding_state(db) -> dict:
    with db.engine.connect() as connection:
        return {
            row[0]: row[1]
            for row in connection.execute(text("SELECT id, embedding FROM news_events_v2"))
        }


CLEARED = (None, None, None, None)


# --------------------------------------------------------------------------- #
# 1 / 2 -- Article retention window
# --------------------------------------------------------------------------- #

def test_article_older_than_14_days_is_deleted(tmp_path):
    db = make_db(tmp_path)
    add_article(db, "old-superseded", days_old=30, is_latest=False, version_no=1)
    report = biweekly_cleanup(db, now=NOW)
    assert report.stage("articles").candidates == 1
    assert report.stage("articles").affected == 1
    assert count_rows(db, "article_versions_v2") == 0


def test_article_within_14_days_is_kept(tmp_path):
    db = make_db(tmp_path)
    add_article(db, "fresh-superseded", days_old=5, is_latest=False, version_no=1)
    add_article(db, "edge-superseded", days_old=13, is_latest=False, version_no=1, family_key="fam2")
    report = biweekly_cleanup(db, now=NOW)
    assert report.stage("articles").candidates == 0
    assert count_rows(db, "article_versions_v2") == 2


def test_old_latest_article_without_event_can_be_deleted(tmp_path):
    """An old *current* version is disposable: currency is not an expiry signal."""
    db = make_db(tmp_path)
    add_article(db, "old-latest", days_old=15, is_latest=True, version_no=2)
    report = biweekly_cleanup(db, now=NOW)
    assert report.stage("articles").candidates == 1
    assert report.stage("articles").affected == 1
    assert count_rows(db, "article_versions_v2") == 0


def test_latest_and_superseded_behave_identically(tmp_path):
    """``is_latest`` must not influence the outcome in either direction."""
    db = make_db(tmp_path)
    add_article(db, "old-latest", days_old=20, is_latest=True, version_no=2, family_key="f1")
    add_article(db, "old-superseded", days_old=20, is_latest=False, version_no=1, family_key="f2")
    add_article(db, "fresh-latest", days_old=2, is_latest=True, version_no=2, family_key="f3")
    add_article(db, "fresh-superseded", days_old=2, is_latest=False, version_no=1, family_key="f4")

    biweekly_cleanup(db, now=NOW)

    with db.engine.connect() as connection:
        survivors = {row[0] for row in connection.execute(text("SELECT id FROM article_versions_v2"))}
    assert survivors == {"fresh-latest", "fresh-superseded"}


# --------------------------------------------------------------------------- #
# 3 -- Event reference protection
# --------------------------------------------------------------------------- #

def test_article_referenced_by_an_event_is_protected(tmp_path):
    db = make_db(tmp_path)
    add_article(db, "cited", days_old=30, is_latest=False, version_no=1)
    add_event(db, "event-live", days_old=1)
    link(db, "event-live", "cited")

    report = biweekly_cleanup(db, now=NOW)

    assert report.stage("articles").candidates == 0
    assert count_rows(db, "article_versions_v2") == 1
    assert count_rows(db, "event_articles_v2") == 1


def test_event_reference_wins_over_age(tmp_path):
    """An article cited by an event that is itself still inside its window stays."""
    db = make_db(tmp_path)
    add_article(db, "cited-old", days_old=200, is_latest=False, version_no=1)
    add_event(db, "event-27d", days_old=27)
    link(db, "event-27d", "cited-old")

    report = biweekly_cleanup(db, now=NOW)

    assert report.stage("events").affected == 0
    assert report.stage("articles").affected == 0
    assert count_rows(db, "article_versions_v2") == 1


# --------------------------------------------------------------------------- #
# 4 / 5 -- Event retention and link removal
# --------------------------------------------------------------------------- #

def test_event_older_than_28_days_is_deleted(tmp_path):
    db = make_db(tmp_path)
    add_event(db, "event-29d", days_old=29)
    report = biweekly_cleanup(db, now=NOW)
    assert report.stage("events").candidates == 1
    assert report.stage("events").affected == 1
    assert count_rows(db, "news_events_v2") == 0


def test_event_within_28_days_is_kept(tmp_path):
    db = make_db(tmp_path)
    add_event(db, "event-27d", days_old=27)
    add_event(db, "event-1d", days_old=1)
    report = biweekly_cleanup(db, now=NOW)
    assert report.stage("events").candidates == 0
    assert count_rows(db, "news_events_v2") == 2


def test_breaking_event_is_deleted_by_age_too(tmp_path):
    """The policy is purely time based: no importance or breaking exemption."""
    db = make_db(tmp_path)
    add_event(db, "event-breaking", days_old=40, breaking=True, importance=99)
    report = biweekly_cleanup(db, now=NOW)
    assert report.stage("events").affected == 1
    assert count_rows(db, "news_events_v2") == 0


def test_deleting_an_event_removes_its_link_rows(tmp_path):
    db = make_db(tmp_path)
    add_article(db, "cited", days_old=40, is_latest=True, version_no=1)
    add_event(db, "event-40d", days_old=40)
    link(db, "event-40d", "cited")

    report = biweekly_cleanup(db, now=NOW)

    assert report.stage("events").affected == 1
    assert report.stage("event_links").candidates == 1
    assert report.stage("event_links").affected == 1
    assert count_rows(db, "news_events_v2") == 0
    assert count_rows(db, "event_articles_v2") == 0
    # No orphaned relationship may survive.
    with db.engine.connect() as connection:
        orphans = connection.execute(text("""
            SELECT COUNT(*) FROM event_articles_v2 ea
            LEFT JOIN news_events_v2 e ON e.id = ea.event_id
            LEFT JOIN article_versions_v2 a ON a.id = ea.article_id
            WHERE e.id IS NULL OR a.id IS NULL
        """)).scalar_one()
    assert orphans == 0


def test_expired_event_releases_its_superseded_article_in_the_same_run(tmp_path):
    """Event first, then the article it was holding -- one pass is enough."""
    db = make_db(tmp_path)
    add_article(db, "held", days_old=40, is_latest=False, version_no=1)
    add_event(db, "event-40d", days_old=40)
    link(db, "event-40d", "held")

    report = biweekly_cleanup(db, now=NOW)

    assert report.stage("event_links").affected == 1
    assert report.stage("articles").affected == 1
    assert count_rows(db, "article_versions_v2") == 0


# --------------------------------------------------------------------------- #
# 6 -- AI logs
# --------------------------------------------------------------------------- #

def test_ai_logs_older_than_90_days_are_deleted(tmp_path):
    db = make_db(tmp_path)
    add_ai_decision(db, "decision-old", days_old=91)
    add_ai_decision(db, "decision-recent", days_old=10)
    add_ai_failure(db, "failure-old", days_old=91)
    add_ai_failure(db, "failure-recent", days_old=10)

    report = biweekly_cleanup(db, now=NOW)

    assert report.stage("ai_decisions").candidates == 1
    assert report.stage("ai_decisions").affected == 1
    assert report.stage("ai_failures").candidates == 1
    assert report.stage("ai_failures").affected == 1
    assert count_rows(db, "ai_decisions_v2") == 1
    assert count_rows(db, "ai_failures_v3") == 1


# --------------------------------------------------------------------------- #
# 7 -- delivery and digest history
# --------------------------------------------------------------------------- #

def test_delivery_and_digest_history_are_trimmed_to_30_days(tmp_path):
    db = make_db(tmp_path)
    add_delivery(db, "breaking:old", days_old=40)
    add_delivery(db, "breaking:recent", days_old=5)
    add_digest_row(db, "morning:2026-08-01", "event-a", days_old=40)
    add_digest_row(db, "morning:2026-10-01", "event-b", days_old=5)

    report = biweekly_cleanup(db, now=NOW)

    assert report.stage("deliveries").affected == 1
    assert report.stage("digest_history").affected == 1
    assert count_rows(db, "deliveries") == 1
    assert count_rows(db, "digest_event_history_v3") == 1


def test_deliveries_survive_the_event_they_refer_to(tmp_path):
    """deliveries has no FK; trimming it is driven by its own timestamp only."""
    db = make_db(tmp_path)
    add_event(db, "event-40d", days_old=40)
    add_delivery(db, "breaking:event-40d", days_old=5)
    report = biweekly_cleanup(db, now=NOW)
    assert report.stage("events").affected == 1
    assert report.stage("deliveries").candidates == 0
    assert count_rows(db, "deliveries") == 1


# --------------------------------------------------------------------------- #
# Embedding lifecycle
# --------------------------------------------------------------------------- #

def test_no_surviving_embedding_is_ever_rewritten_or_refreshed(tmp_path):
    """The invariant: a surviving vector is either byte-identical or cleared as a whole."""
    db = make_db(tmp_path)
    add_article(db, "reachable", days_old=1, cluster_status="embedded", embedding=[0.1, 0.2])
    add_article(db, "unreachable", days_old=1, cluster_status="clustered", embedding=[0.3, 0.4])
    before = embedding_state(db)

    report = biweekly_cleanup(db, now=NOW)

    after = embedding_state(db)
    assert set(after) == set(before)                        # this setup deletes no article
    assert after["reachable"] == before["reachable"]         # still in use -> never touched
    assert after["unreachable"] == CLEARED                   # unreachable -> switched off together
    assert report.stage("orphan_embeddings").affected == 1
    for article_id, columns in after.items():
        # never a partial edit: the four columns move together
        assert columns == CLEARED or all(value is not None for value in columns), article_id


@pytest.mark.parametrize("is_latest", [True, False])
def test_deleted_article_takes_its_embedding_with_it(tmp_path, is_latest):
    """Identical behaviour for a current and a superseded version."""
    db = make_db(tmp_path)
    add_article(db, "doomed", days_old=30, is_latest=is_latest, version_no=1, embedding=[0.1, 0.2])

    report = biweekly_cleanup(db, now=NOW)

    assert report.stage("articles").affected == 1
    assert count_rows(db, "article_versions_v2") == 0
    assert embedding_of(db, "doomed") is None


def test_deleting_an_event_removes_its_embedding(tmp_path):
    """news_events_v2.embedding sits on the event row, so it goes with the event."""
    db = make_db(tmp_path)
    add_event(db, "event-expired", days_old=40)
    add_event(db, "event-live", days_old=1)
    before = event_embedding_state(db)

    report = biweekly_cleanup(db, now=NOW)

    after = event_embedding_state(db)
    assert report.stage("events").affected == 1
    assert "event-expired" not in after
    assert after["event-live"] == before["event-live"]       # the survivor is untouched


def test_pending_article_keeps_its_embedding_for_reuse(tmp_path):
    """The pipeline must still see the cached vector, i.e. no re-embed is triggered."""
    db = make_db(tmp_path)
    add_article(db, "pending", days_old=1, cluster_status="embedded", embedding=[0.7, 0.8])
    biweekly_cleanup(db, now=NOW)
    pending = db.pending_articles()
    assert [item["id"] for item in pending] == ["pending"]
    assert pending[0]["embedding"] == [0.7, 0.8]


def test_orphan_embedding_compaction_is_on_by_default(tmp_path):
    db = make_db(tmp_path)
    add_article(db, "clustered", days_old=1, cluster_status="clustered", embedding=[0.1, 0.2])

    report = biweekly_cleanup(db, now=NOW)

    assert report.stage("orphan_embeddings").candidates == 1
    assert report.stage("orphan_embeddings").affected == 1
    assert tuple(embedding_of(db, "clustered")) == CLEARED


def test_orphan_embedding_compaction_can_be_switched_off(tmp_path):
    db = make_db(tmp_path)
    add_article(db, "clustered", days_old=1, cluster_status="clustered", embedding=[0.1, 0.2])

    report = biweekly_cleanup(db, now=NOW, policy=MaintenancePolicy(compact_orphan_embeddings=False))

    assert "orphan_embeddings" not in [stage.name for stage in report.stages]
    assert embedding_of(db, "clustered").embedding is not None


def test_compaction_leaves_reachable_embeddings_alone(tmp_path):
    db = make_db(tmp_path)
    add_article(db, "pending", days_old=1, cluster_status="embedded", embedding=[0.9, 0.9])
    report = biweekly_cleanup(db, now=NOW)
    assert report.stage("orphan_embeddings").candidates == 0
    assert embedding_of(db, "pending").embedding is not None


def test_cli_no_compact_flag_keeps_orphan_embeddings(tmp_path):
    from daily_news.maintenance import main

    url = "sqlite:///" + str(tmp_path / "cli-nocompact.db")
    db = Database(url)
    db.initialize()
    add_article(db, "clustered", days_old=1, cluster_status="clustered", embedding=[0.1, 0.2])

    assert main(["--no-compact-orphan-embeddings", "--database-url", url]) == 0
    assert embedding_of(Database(url), "clustered").embedding is not None


# --------------------------------------------------------------------------- #
# 8 / 9 -- dry-run and idempotency
# --------------------------------------------------------------------------- #

def test_dry_run_reports_counts_without_touching_the_database(tmp_path):
    db = make_db(tmp_path)
    add_article(db, "doomed", days_old=30, is_latest=False, version_no=1, embedding=[0.1, 0.2])
    add_event(db, "event-40d", days_old=40)
    link(db, "event-40d", "keep-me")
    add_article(db, "keep-me", days_old=2, is_latest=True)
    # survives the article stage, but its vector is unreachable -> a compaction target
    add_article(db, "clustered-keep", days_old=2, cluster_status="clustered", embedding=[0.7, 0.8])
    add_ai_decision(db, "decision-old", days_old=91)
    add_delivery(db, "breaking:old", days_old=40)
    before = database_snapshot(db)
    before_embeddings = embedding_state(db)

    report = biweekly_cleanup(db, dry_run=True, now=NOW)

    assert report.dry_run is True
    assert report.stage("articles").candidates == 1
    assert report.stage("articles").affected == 0
    assert report.stage("events").candidates == 1
    assert report.stage("event_links").candidates == 1
    # dry-run deletes nothing, so "doomed" is still present and its superseded vector
    # is counted here as well: 2 candidates, 0 removed.
    assert report.stage("orphan_embeddings").candidates == 2
    assert report.stage("orphan_embeddings").affected == 0
    assert report.stage("ai_decisions").candidates == 1
    assert report.stage("deliveries").candidates == 1
    assert report.total_affected == 0
    assert database_snapshot(db) == before
    assert embedding_state(db) == before_embeddings


def test_running_twice_is_idempotent(tmp_path):
    db = make_db(tmp_path)
    add_article(db, "doomed", days_old=30, is_latest=False, version_no=1, embedding=[0.1, 0.2])
    add_event(db, "event-40d", days_old=40)
    link(db, "event-40d", "survivor")
    add_article(db, "survivor", days_old=2, is_latest=True)
    add_ai_decision(db, "decision-old", days_old=91)
    add_ai_failure(db, "failure-old", days_old=91)
    add_delivery(db, "breaking:old", days_old=40)

    first = biweekly_cleanup(db, now=NOW)
    second = biweekly_cleanup(db, now=NOW)

    assert first.stage("events").affected == 1
    assert first.stage("event_links").affected == 1
    assert first.stage("articles").affected == 1
    assert first.stage("ai_decisions").affected == 1
    assert first.stage("ai_failures").affected == 1
    assert first.stage("deliveries").affected == 1
    assert first.total_affected == 6

    assert second.total_affected == 0
    for stage in second.stages:
        assert stage.candidates == 0
        assert stage.affected == 0


def test_report_is_json_serialisable(tmp_path):
    db = make_db(tmp_path)
    add_article(db, "doomed", days_old=30, is_latest=False, version_no=1)
    payload = biweekly_cleanup(db, dry_run=True, now=NOW).to_dict()
    assert payload["dry_run"] is True
    assert json.dumps(payload)  # must not raise
    assert payload["stages"][0]["name"] == "events"
    assert {"article", "event", "delivery", "ai_log"} <= set(payload["cutoffs"])


def test_cli_dry_run_does_not_modify_the_database(tmp_path, capsys):
    from daily_news.maintenance import main

    url = "sqlite:///" + str(tmp_path / "cli.db")
    db = Database(url)
    db.initialize()
    add_article(db, "doomed", days_old=30, is_latest=False, version_no=1)
    before = database_snapshot(db)

    assert main(["--dry-run", "--database-url", url]) == 0
    output = capsys.readouterr().out
    assert "DRY-RUN" in output
    assert database_snapshot(Database(url)) == before


def test_vacuum_option_runs_and_keeps_results(tmp_path):
    """``VACUUM`` must not disturb the outcome, only the file size."""
    db = make_db(tmp_path)
    add_article(db, "doomed", days_old=30, is_latest=False, version_no=1, embedding=[0.1, 0.2])
    add_article(db, "kept", days_old=1, cluster_status="embedded", embedding=[0.3, 0.4])

    report = biweekly_cleanup(db, now=NOW, vacuum=True)

    assert report.stage("articles").affected == 1
    assert count_rows(db, "article_versions_v2") == 1
    assert embedding_of(db, "kept").embedding is not None


@pytest.mark.parametrize("is_latest", [True, False])
@pytest.mark.parametrize("days,expected", [
    (14, 0),     # exactly on the boundary is not "older than"
    (15, 1),
])
def test_retention_boundary_is_exclusive(tmp_path, days, expected, is_latest):
    db = make_db(tmp_path)
    add_article(db, "a", days_old=days, is_latest=is_latest, version_no=1)
    report = biweekly_cleanup(db, now=NOW)
    assert report.stage("articles").candidates == expected


@pytest.mark.parametrize("days,expected", [(28, 0), (29, 1)])
def test_event_retention_boundary_is_exclusive(tmp_path, days, expected):
    db = make_db(tmp_path)
    add_event(db, "event", days_old=days)
    assert biweekly_cleanup(db, now=NOW).stage("events").candidates == expected
