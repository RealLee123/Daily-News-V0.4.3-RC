from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import text

from daily_news.ai import AIRetryableError
from daily_news.models import Article
from daily_news.pipeline.article_identity import canonicalize_url, make_content_hash, make_family_key
from daily_news.pipeline.event_clusterer import cluster_pending
from daily_news.storage import Database


def settings(**overrides):
    values = dict(
        event_lookback_hours=96, event_candidate_limit=5,
        event_auto_merge_threshold=.88, event_llm_review_threshold=.62,
        ai_max_cluster_reviews_per_run=12, ai_max_assessments_per_run=0,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def article(index=1, *, title=None, summary="facts", source_id="source", guid=None, url=None):
    title = title or f"Headline {index}"
    guid = guid or f"g-{index}"
    url = url or f"https://example.com/{index}"
    canonical = canonicalize_url(url)
    return Article(
        source_id=source_id, source_name=source_id, source_group=source_id,
        source_kind="media", official=False, title=title, url=url,
        canonical_url=canonical, external_id=guid,
        content_hash=make_content_hash(title, summary),
        family_key=make_family_key(source_id, guid, canonical, title),
        category="world", region="Global", published_at=datetime.now(timezone.utc), summary=summary,
    )


class ResumeAI:
    available = True
    model_name = "fake-text"
    embedding_model_name = "fake-embedding"

    def __init__(self, *, match_error=None, forbid_embeddings=False, vector=None):
        self.match_error = match_error
        self.forbid_embeddings = forbid_embeddings
        self.vector = vector or [0.7, 0.714142]
        self.embedding_texts = 0

    def embeddings(self, texts, on_batch=None):
        if self.forbid_embeddings:
            raise AssertionError("cached article embedding was recomputed")
        self.embedding_texts += len(texts)
        values = [self.vector[:] for _ in texts]
        if on_batch:
            on_batch(0, values)
        return values

    def decide_event_match(self, article_data, candidates):
        if self.match_error:
            raise self.match_error
        return {"match": False, "event_id": "", "confidence": .9, "reason": "different"}

    def assess_importance(self, evidence):
        raise AssertionError("importance is disabled in these checkpoint tests")


def seed_gray_candidate(db):
    now = datetime.now(timezone.utc)
    existing = {
        "title": "Existing event", "summary": "", "category": "world", "region": "Global",
        "published_at": now,
    }
    return db.create_event(existing, [1.0, 0.0])


def test_a_embedding_is_reused_after_clustering_failure(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'a.db'}")
    db.initialize()
    seed_gray_candidate(db)
    item = article()
    db.ingest_articles([item])

    first = ResumeAI(match_error=AIRetryableError("temporary cluster failure"))
    result1 = cluster_pending(settings(), db, first)
    assert result1["generated_embeddings"] == 1
    assert db.pending_articles()[0]["embedding"]

    second = ResumeAI(forbid_embeddings=True)
    result2 = cluster_pending(settings(), db, second)
    assert result2["reused_embeddings"] == 1
    assert result2["clustered"] == 1


def test_b_ssl_eof_resumes_from_clustering_checkpoint(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'b.db'}")
    db.initialize()
    seed_gray_candidate(db)
    db.ingest_articles([article()])

    cluster_pending(settings(), db, ResumeAI(match_error=AIRetryableError("SSL EOF during handshake")))
    resumed = ResumeAI(forbid_embeddings=True)
    result = cluster_pending(settings(), db, resumed)
    assert result["reused_embeddings"] == 1
    assert result["remaining_pending"] == 0


def test_c_partial_429_checkpoints_first_fifty(tmp_path):
    class PartialAI(ResumeAI):
        def embeddings(self, texts, on_batch=None):
            self.embedding_texts += len(texts)
            values = [[1.0, 0.0] for _ in texts[:50]]
            on_batch(0, values)
            raise AIRetryableError("429 quota")

    db = Database(f"sqlite:///{tmp_path / 'c.db'}")
    db.initialize()
    db.ingest_articles([article(i, source_id=f"source-{i}") for i in range(120)])
    first = PartialAI()
    result1 = cluster_pending(settings(), db, first)
    assert result1["generated_embeddings"] == 50

    second = ResumeAI(vector=[1.0, 0.0])
    result2 = cluster_pending(settings(), db, second)
    assert second.embedding_texts == 70
    assert result2["generated_embeddings"] == 70
    assert result2["remaining_pending"] == 0


def test_d_old_rss_item_never_reenters_ai(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'd.db'}")
    db.initialize()
    item = article()
    db.ingest_articles([item])
    cluster_pending(settings(), db, ResumeAI(vector=[1.0, 0.0]))
    duplicate = db.ingest_articles([item])
    assert duplicate.exact_duplicates == 1
    result = cluster_pending(settings(), db, ResumeAI(forbid_embeddings=True))
    assert result["pending"] == 0
    assert result["clustered"] == 0


def test_e_same_source_substantive_update_inherits_event_without_embedding(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'e.db'}")
    db.initialize()
    first = article(1, title="UPDATE 1-Iran says rates will change", summary="first", guid="g1", url="https://example.com/a1")
    db.ingest_articles([first])
    cluster_pending(settings(), db, ResumeAI(vector=[1.0, 0.0]))

    revision = article(2, title="CORRECTED-Iran says rates will change", summary="new facts", guid="g2", url="https://example.com/a2")
    ingest = db.ingest_articles([revision])
    assert ingest.new_versions == 1
    result = cluster_pending(settings(), db, ResumeAI(forbid_embeddings=True))
    assert result["clustered"] == 1
    with db.engine.connect() as connection:
        method = connection.execute(text("SELECT link_method FROM event_articles_v2 WHERE article_id=:id"), {"id": revision.id}).scalar_one()
    assert method == "family_version"


def test_v031_database_migrates_without_deleting_rows(tmp_path):
    path = tmp_path / "legacy.db"
    db = Database(f"sqlite:///{path}")
    db.initialize()
    item = article()
    db.ingest_articles([item])
    with db.engine.begin() as connection:
        # Simulate the V0.3.1 article table by rebuilding it without V0.4 columns.
        connection.execute(text("ALTER TABLE article_versions_v2 DROP COLUMN embedding"))
        connection.execute(text("ALTER TABLE article_versions_v2 DROP COLUMN embedding_model"))
        connection.execute(text("ALTER TABLE article_versions_v2 DROP COLUMN embedding_dimensions"))
        connection.execute(text("ALTER TABLE article_versions_v2 DROP COLUMN embedded_at"))
        connection.execute(text("ALTER TABLE article_versions_v2 DROP COLUMN source_tier"))
        connection.execute(text("ALTER TABLE article_versions_v2 DROP COLUMN primary_source"))
        connection.execute(text("ALTER TABLE article_versions_v2 DROP COLUMN default_weight"))
    db.initialize()
    pending = db.pending_articles()
    assert pending[0]["id"] == item.id
    assert pending[0]["embedding"] is None
    assert pending[0]["source_tier"] == "standard"
    assert not pending[0]["primary_source"]
    assert pending[0]["default_weight"] == 1.0
