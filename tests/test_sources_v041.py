import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
from sqlalchemy import text

from daily_news.collectors.registry import collect_all
from daily_news.collectors.public_page import collect_news_sitemap, collect_public_page
from daily_news.collectors.x_api import collect_x_user_timeline
from daily_news.models import Article
from daily_news.pipeline.article_identity import canonicalize_url, make_content_hash, make_family_key
from daily_news.pipeline.breaking import is_breaking_candidate
from daily_news.pipeline.event_clusterer import cluster_pending
from daily_news.source_registry import SourceDefinition, SourceRegistry
from daily_news.storage import Database


def settings(**overrides):
    values = dict(
        event_lookback_hours=96, event_candidate_limit=5,
        event_auto_merge_threshold=.88, event_llm_review_threshold=.62,
        ai_max_cluster_reviews_per_run=12, ai_max_assessments_per_run=0,
        embedding_dimensions=2, breaking_threshold=80,
        breaking_min_confidence=.8, breaking_min_sources=2,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def source(source_id, source_name, group, kind="news_media", official=False, primary=False):
    return dict(
        source_id=source_id, source_name=source_name, source_group=group,
        source_kind=kind, source_tier="high", official=official,
        primary_source=primary, default_weight=1.0,
    )


def article(meta, suffix, title="US announces new tariff measures on China", summary="same policy event"):
    meta = dict(meta)
    meta.setdefault("category", "politics")
    meta.setdefault("region", "United States")
    url = f"https://example.com/{meta['source_id']}/{suffix}"
    canonical = canonicalize_url(url)
    return Article(
        **meta, title=title, url=url, canonical_url=canonical, external_id=suffix,
        content_hash=make_content_hash(title, summary),
        family_key=make_family_key(meta["source_id"], suffix, canonical, title),
        published_at=datetime.now(timezone.utc), summary=summary,
    )


class SameEventAI:
    available = True
    model_name = "fake-text"
    embedding_model_name = "fake-embedding"

    def embeddings(self, texts, on_batch=None):
        vectors = [[1.0, 0.0] for _ in texts]
        if on_batch:
            on_batch(0, vectors)
        return vectors

    def decide_event_match(self, *_):
        raise AssertionError("identical vectors should auto-merge")

    def assess_importance(self, *_):
        raise AssertionError("importance disabled")


def clustered_payload(tmp_path, items):
    db = Database(f"sqlite:///{tmp_path / 'sources.db'}")
    db.initialize()
    db.ingest_articles(items)
    result = cluster_pending(settings(), db, SameEventAI())
    assert len(result["event_ids"]) == 1
    return db, db.event_payload(result["event_ids"][0])


def test_reuters_and_bloomberg_merge_into_one_event_with_two_evidence(tmp_path):
    items = [
        article(source("reuters", "Reuters", "reuters"), "r1"),
        article(source("bloomberg", "Bloomberg", "bloomberg"), "b1"),
    ]
    _, event = clustered_payload(tmp_path, items)
    assert event["article_count"] == 2
    assert event["independent_source_count"] == 2
    assert {item["source_id"] for item in event["articles"]} == {"reuters", "bloomberg"}


def test_trump_white_house_reuters_merge_into_one_event(tmp_path):
    items = [
        article(source("donald-trump-x", "Donald Trump (@realDonaldTrump)", "donald_trump", "social_primary", primary=True), "x1", title="Donald Trump 在 X 表示：将宣布新的中国关税"),
        article(source("white-house-actions", "White House Presidential Actions", "white_house", "official", True, True), "w1"),
        article(source("reuters", "Reuters", "reuters"), "r1"),
    ]
    _, event = clustered_payload(tmp_path, items)
    assert event["article_count"] == 3
    assert event["independent_source_count"] == 3


def test_white_house_sections_share_owner_and_do_not_inflate_source_count(tmp_path):
    items = [
        article(source("white-house-briefings", "White House Briefings", "white_house", "official", True, True), "w1"),
        article(source("white-house-actions", "White House Actions", "white_house", "official", True, True), "w2"),
    ]
    _, event = clustered_payload(tmp_path, items)
    assert event["article_count"] == 2
    assert event["independent_source_count"] == 1
    assert event["official_source_count"] == 1


def test_trump_x_attribution_and_duplicate_post_id(tmp_path):
    definition = SourceDefinition(
        source_id="donald-trump-x", source_name="Donald Trump (@realDonaldTrump)",
        source_type="social_primary", source_tier="primary", source_group="donald_trump",
        official=False, primary_source=True, default_weight=.9, fetch_method="x_api_v2",
        enabled=True, region="United States", category="politics", options={"username": "realDonaldTrump"},
    )

    def handler(request):
        if "/by/username/" in request.url.path:
            return httpx.Response(200, json={"data": {"id": "25073877"}})
        return httpx.Response(200, json={"data": [{
            "id": "123456789", "text": "I will impose a 50% tariff.", "created_at": "2026-10-03T01:00:00Z",
        }]})

    async def fetch():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await collect_x_user_timeline(client, definition, "test-token")

    fetched = asyncio.run(fetch())
    assert fetched[0].external_id == "123456789"
    assert fetched[0].source_kind == "social_primary"
    assert "在 X 表示" in fetched[0].title
    assert "不代表相关政策已经正式生效" in fetched[0].summary

    db = Database(f"sqlite:///{tmp_path / 'x.db'}")
    db.initialize()
    first = db.ingest_articles(fetched)
    second = db.ingest_articles(fetched)
    assert len(first.inserted_ids) == 1
    assert second.exact_duplicates == 1
    assert len(db.pending_articles()) == 1


def test_source_failure_is_isolated_and_other_sources_continue(tmp_path, monkeypatch):
    config = [
        {"source_id":"reuters","source_name":"Reuters","source_type":"news_media","source_tier":"high","source_group":"reuters","official":False,"primary_source":False,"default_weight":1,"fetch_method":"news_sitemap","enabled":True,"region":"Global","category":"world","url":"https://reuters.invalid"},
        {"source_id":"bloomberg","source_name":"Bloomberg","source_type":"news_media","source_tier":"high","source_group":"bloomberg","official":False,"primary_source":False,"default_weight":1,"fetch_method":"public_page","enabled":True,"region":"Global","category":"business","url":"https://bloomberg.invalid"},
        {"source_id":"white-house-releases","source_name":"White House Releases","source_type":"official","source_tier":"high","source_group":"white_house","official":True,"primary_source":True,"default_weight":1,"fetch_method":"public_page","enabled":True,"region":"United States","category":"politics","url":"https://whitehouse.invalid"}
    ]
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(config), "utf-8")

    async def fake_collect(client, definition, source_settings):
        if definition.source_id == "reuters":
            raise httpx.ConnectError("Reuters unavailable")
        return [article(definition.article_metadata(), definition.source_id)]

    monkeypatch.setattr("daily_news.collectors.registry._collect_one", fake_collect)
    articles, errors = asyncio.run(collect_all(path, 1, SimpleNamespace()))
    assert {item.source_id for item in articles} == {"bloomberg", "white-house-releases"}
    assert len(errors) == 1 and errors[0].startswith("Reuters:")


def test_trump_x_without_credentials_is_disabled_without_affecting_registry(tmp_path):
    path = tmp_path / "sources.json"
    path.write_text(json.dumps([{
        "source_id":"donald-trump-x","source_name":"Trump X","source_type":"social_primary",
        "source_tier":"primary","source_group":"donald_trump","official":False,
        "primary_source":True,"default_weight":.9,"fetch_method":"x_api_v2","enabled":False,
        "enabled_setting":"trump_x_enabled","region":"United States","category":"politics","url":"https://x.com/realDonaldTrump"
    }]), "utf-8")
    registry = SourceRegistry.load(path, SimpleNamespace(trump_x_enabled=False, x_bearer_token=""))
    assert registry.enabled() == []


def test_pending_breakdown_and_single_breaking_gate(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'pending.db'}")
    db.initialize()
    db.ingest_articles([article(source("reuters", "Reuters", "reuters"), "r1")])
    counts = db.pending_counts()
    assert counts == {"article_pipeline": 1, "event_assessment": 0, "total": 1}

    event = {
        "breaking": True, "importance": 90, "confidence": .95, "status": "confirmed",
        "independent_source_count": 1, "official_source_count": 0,
    }
    assert not is_breaking_candidate(event, settings())
    event["official_source_count"] = 1
    assert is_breaking_candidate(event, settings())


def test_reuters_official_sitemap_adapter_uses_article_loc_not_image_loc():
    xml = b'''<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
      xmlns:news="http://www.google.com/schemas/sitemap-news/0.9" xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
      <url><loc>https://www.reuters.com/world/example-story-2026-10-03/</loc>
      <news:news><news:publication_date>2026-10-03T05:00:00Z</news:publication_date><news:title>Example Reuters story</news:title></news:news>
      <image:image><image:loc>https://www.reuters.com/resizer/example.jpg</image:loc></image:image></url></urlset>'''
    definition = SourceDefinition(
        source_id="reuters", source_name="Reuters", source_type="news_media", source_tier="high",
        source_group="reuters", official=False, primary_source=False, default_weight=1,
        fetch_method="news_sitemap", enabled=True, region="Global", category="world",
        url="https://www.reuters.com/sitemap.xml", options={"allowed_hosts": ["www.reuters.com"]},
    )

    async def fetch():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=xml))) as client:
            return await collect_news_sitemap(client, definition)

    result = asyncio.run(fetch())
    assert len(result) == 1
    assert result[0].canonical_url == "https://www.reuters.com/world/example-story-2026-10-03"


def test_white_house_public_page_adapter_reads_official_timestamp():
    html = '''<li><h2><a href="https://www.whitehouse.gov/releases/2026/10/example/">White House announces example action</a></h2>
      <div class="post-date"><time datetime="2026-10-02T14:28:12-04:00">October 2, 2026</time></div></li>'''
    definition = SourceDefinition(
        source_id="white-house-releases", source_name="White House Releases", source_type="official",
        source_tier="high", source_group="white_house", official=True, primary_source=True,
        default_weight=1, fetch_method="public_page", enabled=True, region="United States",
        category="politics", url="https://www.whitehouse.gov/releases/",
        options={"allowed_hosts": ["www.whitehouse.gov"], "path_prefixes": ["/releases/"]},
    )

    async def fetch():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=html))) as client:
            return await collect_public_page(client, definition)

    result = asyncio.run(fetch())
    assert len(result) == 1
    assert result[0].published_at.isoformat() == "2026-10-02T18:28:12+00:00"
