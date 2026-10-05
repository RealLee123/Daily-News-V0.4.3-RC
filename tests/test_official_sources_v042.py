import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx

from daily_news.collectors.bloomberg_latest import BloombergLatestAdapter
from daily_news.collectors.registry import collect_all
from daily_news.models import Article
from daily_news.pipeline.article_identity import canonicalize_url, make_content_hash, make_family_key
from daily_news.pipeline.event_clusterer import cluster_pending
from daily_news.source_registry import SourceDefinition
from daily_news.storage import Database


BLOOMBERG_XML = b'''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
 xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
 <url>
  <loc>https://www.bloomberg.com/news/articles/2026-10-03/example-story?utm_source=test</loc>
  <lastmod>2026-10-03T05:02:00.000Z</lastmod>
  <news:news>
   <news:publication><news:name>Bloomberg News</news:name><news:language>en</news:language></news:publication>
   <news:publication_date>2026-10-03T05:00:00.000Z</news:publication_date>
   <news:title>Bloomberg Latest example story</news:title>
  </news:news>
 </url>
</urlset>'''


def bloomberg_definition(**overrides):
    values = dict(
        source_id="bloomberg", source_name="Bloomberg", source_type="news_media",
        source_tier="high", source_group="bloomberg", official=False,
        primary_source=False, default_weight=1.0,
        fetch_method="bloomberg_latest_sitemap", enabled=True, region="Global",
        category="business", url="https://www.bloomberg.com/sitemaps/news/latest.xml",
        options={"allowed_hosts": ["www.bloomberg.com"],
                 "path_prefixes": ["/news/articles/", "/opinion/articles/"],
                 "max_articles": 100},
    )
    values.update(overrides)
    return SourceDefinition(**values)


async def fetch_bloomberg(xml=BLOOMBERG_XML, status=200):
    transport = httpx.MockTransport(lambda request: httpx.Response(status, content=xml))
    async with httpx.AsyncClient(transport=transport) as client:
        return await BloombergLatestAdapter(bloomberg_definition()).collect(client)


def pipeline_settings(**overrides):
    values = dict(
        event_lookback_hours=96, event_candidate_limit=5,
        event_auto_merge_threshold=.88, event_llm_review_threshold=.62,
        ai_max_cluster_reviews_per_run=12, ai_max_assessments_per_run=0,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class CountingAI:
    available = True
    model_name = "fake-text"
    embedding_model_name = "fake-embedding"

    def __init__(self):
        self.embedding_texts = 0

    def embeddings(self, texts, on_batch=None):
        self.embedding_texts += len(texts)
        vectors = [[1.0, 0.0] for _ in texts]
        if on_batch:
            on_batch(0, vectors)
        return vectors

    def decide_event_match(self, *_):
        return {"match": False, "event_id": "", "confidence": .9, "reason": "different"}

    def assess_importance(self, *_):
        raise AssertionError("importance disabled")


def make_article(source_id, source_name, source_group, suffix, title):
    url = f"https://example.com/{source_id}/{suffix}"
    canonical = canonicalize_url(url)
    return Article(
        source_id=source_id, source_name=source_name, source_group=source_group,
        source_kind="news_media", source_tier="high", official=False,
        primary_source=False, default_weight=1.0, title=title, url=url,
        canonical_url=canonical, external_id=suffix,
        content_hash=make_content_hash(title, "same event"),
        family_key=make_family_key(source_id, suffix, canonical, title),
        category="world", region="Global", published_at=datetime.now(timezone.utc),
        summary="same event",
    )


def test_bloomberg_latest_sitemap_generates_article():
    result = asyncio.run(fetch_bloomberg())
    assert len(result) == 1
    item = result[0]
    assert item.source_id == "bloomberg"
    assert item.title == "Bloomberg Latest example story"
    assert item.published_at.isoformat() == "2026-10-03T05:00:00+00:00"
    assert item.canonical_url == "https://www.bloomberg.com/news/articles/2026-10-03/example-story"
    assert item.external_id == item.canonical_url


def test_bloomberg_second_run_exact_dedupe_never_reembeds(tmp_path):
    item = asyncio.run(fetch_bloomberg())[0]
    db = Database(f"sqlite:///{tmp_path / 'bloomberg.db'}")
    db.initialize()
    db.ingest_articles([item])
    first_ai = CountingAI()
    cluster_pending(pipeline_settings(), db, first_ai)
    assert first_ai.embedding_texts == 1

    duplicate = db.ingest_articles([item])
    assert duplicate.exact_duplicates == 1
    second_ai = CountingAI()
    result = cluster_pending(pipeline_settings(), db, second_ai)
    assert second_ai.embedding_texts == 0
    assert result["pending"] == 0


def test_bloomberg_and_reuters_can_merge_into_one_event(tmp_path):
    title = "Central bank unexpectedly cuts its benchmark rate"
    items = [
        make_article("bloomberg", "Bloomberg", "bloomberg", "b1", title),
        make_article("reuters", "Reuters", "reuters", "r1", title),
    ]
    db = Database(f"sqlite:///{tmp_path / 'merge.db'}")
    db.initialize()
    db.ingest_articles(items)
    result = cluster_pending(pipeline_settings(), db, CountingAI())
    assert len(result["event_ids"]) == 1
    event = db.event_payload(result["event_ids"][0])
    assert event["article_count"] == 2
    assert event["independent_source_count"] == 2


def test_bloomberg_403_isolated_while_other_source_continues(tmp_path, monkeypatch):
    config = [
        {
            "source_id":"bloomberg", "source_name":"Bloomberg", "source_type":"news_media",
            "source_tier":"high", "source_group":"bloomberg", "official":False,
            "primary_source":False, "default_weight":1.0,
            "fetch_method":"bloomberg_latest_sitemap", "enabled":True,
            "region":"Global", "category":"business",
            "url":"https://www.bloomberg.com/sitemaps/news/latest.xml",
        },
        {"source_id":"other", "source_name":"Other", "source_type":"news_media",
         "source_tier":"standard", "source_group":"other", "official":False,
         "primary_source":False, "default_weight":1.0, "fetch_method":"rss",
         "enabled":True, "region":"Global", "category":"world", "url":"https://other.invalid"},
    ]
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(config), "utf-8")

    async def fake_collect(client, definition, source_settings):
        if definition.source_id == "bloomberg":
            transport = httpx.MockTransport(lambda request: httpx.Response(403, text="Forbidden"))
            async with httpx.AsyncClient(transport=transport) as mock_client:
                return await BloombergLatestAdapter(definition).collect(mock_client)
        return [make_article("other", "Other", "other", "o1", "Other source continues")]

    monkeypatch.setattr("daily_news.collectors.registry._collect_one", fake_collect)
    articles, errors = asyncio.run(collect_all(path, 1, SimpleNamespace()))
    assert [item.source_id for item in articles] == ["other"]
    assert len(errors) == 1 and errors[0].startswith("Bloomberg: HTTPStatusError")


def test_bloomberg_canonical_url_normalization():
    value = canonicalize_url(
        "HTTPS://WWW.BLOOMBERG.COM/news/articles/2026-10-03/example-story/"
        "?utm_source=newsletter&ref=homepage&cmpid=socialflow#fragment"
    )
    assert value == "https://www.bloomberg.com/news/articles/2026-10-03/example-story"


def test_bloomberg_tracking_parameters_do_not_create_duplicate(tmp_path):
    first = asyncio.run(fetch_bloomberg())[0]
    second_xml = BLOOMBERG_XML.replace(
        b"?utm_source=test", b"?utm_medium=email&amp;utm_campaign=latest"
    )
    second = asyncio.run(fetch_bloomberg(second_xml))[0]
    assert first.canonical_url == second.canonical_url
    assert first.id == second.id

    db = Database(f"sqlite:///{tmp_path / 'tracking.db'}")
    db.initialize()
    assert len(db.ingest_articles([first]).inserted_ids) == 1
    assert db.ingest_articles([second]).exact_duplicates == 1
