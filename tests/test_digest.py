from datetime import datetime, timezone
from types import SimpleNamespace

from daily_news.pipeline.digest import prepare_digest_events


def event(event_id, article_ids):
    now = datetime.now(timezone.utc)
    return {
        "id": event_id, "canonical_title": event_id, "summary": "summary", "category": "politics",
        "region": "Global", "first_seen": now, "last_seen": now, "importance": 80,
        "scope": "international", "confidence": .9, "importance_reason": "test",
        "independent_source_count": 2, "official_source_count": 0,
        "articles": [{"id": aid, "source_name": "Source", "title": aid, "published_at": now,
                      "canonical_url": f"https://example.com/{aid}"} for aid in article_ids],
    }


def test_cross_edition_only_repeats_substantive_updates():
    events = [event("new", ["n1"]), event("same", ["s1"]), event("update", ["u1", "u2"])]

    class DB:
        def events_between(self, *args): return events
        def latest_digest_snapshot(self, event_id):
            if event_id == "new": return None
            return {"article_ids": ["s1"] if event_id == "same" else ["u1"]}
        def digest_snapshot(self, item): return {"article_ids": [a["id"] for a in item["articles"]]}
        def record_ai_failure(self, *args): raise AssertionError("no failure expected")
        def record_ai_decision(self, *args): pass

    class AI:
        model_name = "fake"
        def assess_substantive_update(self, previous, current):
            return {"substantive": True, "confidence": .9, "update_label": "官方确认", "reason": "new fact"}

    selected, stats = prepare_digest_events(SimpleNamespace(max_digest_events=10), DB(), AI(), None, None)
    assert {item["id"] for item in selected} == {"new", "update"}
    assert next(item for item in selected if item["id"] == "update")["is_update"] is True
    assert stats["repeated_skipped"] == 1
    assert stats["updates"] == 1
