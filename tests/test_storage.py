from datetime import datetime, timezone

from daily_news.ai import AIRetryableError
from daily_news.models import Article
from daily_news.pipeline.article_identity import canonicalize_url, make_content_hash, make_family_key
from daily_news.pipeline.event_clusterer import cluster_pending
from daily_news.storage import Database


def article(title="Headline", summary="First", url="https://example.com/a", guid="g1"):
    return Article(
        source_id="source", source_name="Source", source_group="Source", source_kind="media",
        official=False, title=title, url=url, canonical_url=canonicalize_url(url), external_id=guid,
        content_hash=make_content_hash(title, summary),
        family_key=make_family_key("source", guid, canonicalize_url(url), title),
        category="world", region="Global", published_at=datetime.now(timezone.utc), summary=summary,
    )


def test_exact_duplicate_and_same_source_revision(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'test.db'}")
    db.initialize()
    first = article("UPDATE 1-Iran says rates will change", "First", "https://example.com/a1", "g1")
    duplicate = article("UPDATE 1-Iran says rates will change", "First", "https://example.com/a1?utm_source=x", "g1")
    revision = article("UPDATE 3-Iran says rates will change", "New facts", "https://example.com/a3", "g3")
    one = db.ingest_articles([first, duplicate])
    two = db.ingest_articles([revision])
    assert len(one.inserted_ids) == 1
    assert one.exact_duplicates == 1
    assert two.new_versions == 1
    assert len(db.pending_articles()) == 1
    assert db.pending_articles()[0]["title"].startswith("UPDATE 3")


def test_retryable_embedding_error_keeps_real_database_article_pending(tmp_path):
    class FailingAI:
        available = True
        model_name = "fake"
        embedding_model_name = "fake-embedding"

        def embeddings(self, texts, on_batch=None):
            raise AIRetryableError("429 quota")

    class Settings:
        event_lookback_hours = 96
        event_candidate_limit = 5
        event_auto_merge_threshold = .88
        event_llm_review_threshold = .62

    db = Database(f"sqlite:///{tmp_path / 'test.db'}")
    db.initialize()
    db.ingest_articles([article()])
    result = cluster_pending(Settings(), db, FailingAI())
    assert result["remaining_pending"] == 1
    assert len(db.pending_articles()) == 1
