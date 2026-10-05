from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from daily_news.ai import AIRetryableError
from daily_news.jobs import _edition_window
from daily_news.pipeline.article_identity import canonicalize_url, make_content_hash, make_family_key, normalized_family_title
from daily_news.pipeline.event_clusterer import cluster_pending
from daily_news.pipeline.semantic import cosine_similarity


class IdentityTest(unittest.TestCase):
    def test_canonical_url_removes_tracking_and_fragment(self):
        actual = canonicalize_url("HTTPS://Example.com/news/123/?utm_source=x&b=2&a=1#top")
        self.assertEqual(actual, "https://example.com/news/123?a=1&b=2")

    def test_update_prefix_is_not_part_of_family_title(self):
        self.assertEqual(normalized_family_title("UPDATE 3-Iran says rates will change"), "iran says rates will change")

    def test_content_hash_changes_when_story_changes(self):
        self.assertNotEqual(make_content_hash("A", "first"), make_content_hash("A", "updated"))
        self.assertEqual(
            make_family_key("reuters", "guid-1", "https://example.com/a1", "UPDATE 1-Iran says rates will change"),
            make_family_key("reuters", "guid-2", "https://example.com/a3", "UPDATE 3-Iran says rates will change"),
        )


class SemanticTest(unittest.TestCase):
    def test_cosine_similarity(self):
        self.assertAlmostEqual(cosine_similarity([1, 0], [0.99, 0.01]), 0.9999, places=3)
        self.assertEqual(cosine_similarity([1, 0], [0, 1]), 0.0)


class FakeAI:
    available = True
    model_name = "fake"
    embedding_model_name = "fake-embedding"

    def embeddings(self, texts, on_batch=None):
        values = [[1.0, 0.0], [0.0, 1.0]][:len(texts)]
        if on_batch:
            on_batch(0, values)
        return values

    def decide_event_match(self, article, candidates):
        raise AssertionError("clear similarities should not call the LLM match judge")

    def assess_importance(self, evidence):
        return {
            "canonical_title": evidence["event"]["title"], "summary": "summary",
            "category": "politics", "region": "Global", "importance": 70,
            "breaking": False, "scope": "international", "confidence": 0.9,
            "reason": "test", "stale_or_background": False, "needs_more_sources": False,
        }


class FakeDB:
    def __init__(self):
        now = datetime.now(timezone.utc)
        self.pending = [
            {"id": "a1", "title": "same event", "summary": "", "source_name": "A", "source_group": "A",
             "source_kind": "media", "official": False, "region": "Global", "category": "world",
             "published_at": now, "source_id": "a", "family_key": "f1", "embedding": None},
            {"id": "a2", "title": "new event", "summary": "", "source_name": "B", "source_group": "B",
             "source_kind": "media", "official": False, "region": "Global", "category": "world",
             "published_at": now, "source_id": "b", "family_key": "f2", "embedding": None},
        ]
        self.events = [{"id": "e1", "canonical_title": "same", "summary": "", "category": "world",
                        "region": "Global", "last_seen": now, "embedding": [1.0, 0.0], "article_count": 1}]
        self.links = []
        self.failures = []
        self.clustered = set()

    def pending_articles(self, limit): return [item for item in self.pending if item["id"] not in self.clustered]
    def save_article_embeddings(self, rows, model):
        by_id = dict(rows)
        for item in self.pending:
            if item["id"] in by_id:
                item["embedding"] = by_id[item["id"]]
                item["embedding_model"] = model
                item["embedding_dimensions"] = len(by_id[item["id"]])
    def event_for_family(self, source_id, family_key): return None
    def recent_events(self, hours): return self.events
    def create_event(self, article, embedding):
        event_id = "e2"
        self.events.append({"id": event_id, "canonical_title": article["title"], "summary": "",
                            "category": "world", "region": "Global", "last_seen": article["published_at"],
                            "embedding": embedding, "article_count": 0})
        return event_id
    def update_event_embedding(self, event_id, embedding): pass
    def attach_article(self, event_id, article_id, method, confidence):
        self.links.append((event_id, article_id, method)); self.clustered.add(article_id)
    def event_payload(self, event_id):
        event = next(item for item in self.events if item["id"] == event_id)
        return {**event, "first_seen": event["last_seen"], "article_count": 1,
                "independent_source_count": 1, "official_source_count": 0, "velocity_30m": 1,
                "articles": [{"source_name": "A", "source_group": "A", "source_kind": "media",
                              "official": False, "title": event["canonical_title"], "published_at": event["last_seen"]}]}
    def record_ai_decision(self, *args): pass
    def update_assessment(self, event_id, assessment): pass
    def pending_assessment_event_ids(self): return []
    def was_event_breaking_delivered(self, event_id): return False
    def latest_digest_snapshot(self, event_id): return None
    def record_ai_failure(self, work_type, subject_id, error, retryable=True): self.failures.append((work_type, subject_id))
    def mark_assessment_retry(self, event_id, error): pass


class ClusterTest(unittest.TestCase):
    def test_clear_same_and_clear_different_do_not_call_llm_match(self):
        settings = SimpleNamespace(event_lookback_hours=96, event_candidate_limit=5,
                                   event_auto_merge_threshold=.88, event_llm_review_threshold=.62)
        db = FakeDB()
        result = cluster_pending(settings, db, FakeAI())
        self.assertEqual(result["llm_reviews"], 0)
        self.assertIn(("e1", "a1", "embedding_auto"), db.links)
        self.assertIn(("e2", "a2", "new_event"), db.links)

    def test_embedding_failure_leaves_articles_pending(self):
        class FailingAI(FakeAI):
            def embeddings(self, texts, on_batch=None):
                raise AIRetryableError("429 quota")

        settings = SimpleNamespace(event_lookback_hours=96, event_candidate_limit=5,
                                   event_auto_merge_threshold=.88, event_llm_review_threshold=.62)
        db = FakeDB()
        result = cluster_pending(settings, db, FailingAI())
        self.assertEqual(result["clustered"], 0)
        self.assertEqual(result["remaining_pending"], 2)
        self.assertEqual(db.links, [])
        self.assertIn(("embedding", "pending_articles"), db.failures)


class WindowTest(unittest.TestCase):
    def test_morning_and_evening_windows_are_exact(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        settings = SimpleNamespace(timezone="Asia/Shanghai", morning_time="09:30", evening_time="21:30")
        now = datetime(2026, 10, 2, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        morning = _edition_window(settings, "morning", now)
        evening = _edition_window(settings, "evening", now)
        self.assertEqual(morning[0].isoformat(), "2026-10-01T21:30:00+08:00")
        self.assertEqual(morning[1].isoformat(), "2026-10-02T09:30:00+08:00")
        self.assertEqual(evening[0].isoformat(), "2026-10-02T09:30:00+08:00")
        self.assertEqual(evening[1].isoformat(), "2026-10-02T21:30:00+08:00")


if __name__ == "__main__":
    unittest.main()
