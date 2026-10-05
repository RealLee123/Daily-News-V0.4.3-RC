from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Iterable
from uuid import uuid4

from sqlalchemy import create_engine, text

from daily_news.models import Article, Event, IngestResult

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS article_versions_v2 (
        id VARCHAR(32) PRIMARY KEY, source_id TEXT NOT NULL, source_name TEXT NOT NULL,
        source_group TEXT NOT NULL, source_kind TEXT NOT NULL, official BOOLEAN NOT NULL,
        source_tier VARCHAR(24) NOT NULL DEFAULT 'standard', primary_source BOOLEAN NOT NULL DEFAULT false,
        default_weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        external_id TEXT NOT NULL, original_url TEXT NOT NULL, canonical_url TEXT NOT NULL,
        content_hash VARCHAR(64) NOT NULL, family_key VARCHAR(32) NOT NULL,
        title TEXT NOT NULL, summary TEXT NOT NULL, category TEXT NOT NULL, region TEXT NOT NULL,
        published_at TIMESTAMP WITH TIME ZONE NOT NULL, discovered_at TIMESTAMP WITH TIME ZONE NOT NULL,
        version_no INTEGER NOT NULL, is_latest BOOLEAN NOT NULL, cluster_status VARCHAR(24) NOT NULL,
        embedding TEXT, embedding_model TEXT, embedding_dimensions INTEGER, embedded_at TIMESTAMP WITH TIME ZONE
    )""",
    "CREATE INDEX IF NOT EXISTS idx_articles_family_v2 ON article_versions_v2(source_id,family_key,is_latest)",
    "CREATE INDEX IF NOT EXISTS idx_articles_pending_v2 ON article_versions_v2(cluster_status,is_latest,published_at)",
    """CREATE TABLE IF NOT EXISTS news_events_v2 (
        id VARCHAR(36) PRIMARY KEY, canonical_title TEXT NOT NULL, summary TEXT NOT NULL,
        category TEXT NOT NULL, region TEXT NOT NULL,
        first_seen TIMESTAMP WITH TIME ZONE NOT NULL, last_seen TIMESTAMP WITH TIME ZONE NOT NULL,
        embedding TEXT NOT NULL, article_count INTEGER NOT NULL,
        independent_source_count INTEGER NOT NULL, official_source_count INTEGER NOT NULL,
        velocity_30m INTEGER NOT NULL, importance INTEGER, breaking BOOLEAN, scope VARCHAR(24),
        confidence DOUBLE PRECISION, importance_reason TEXT, status VARCHAR(24) NOT NULL,
        assessed_at TIMESTAMP WITH TIME ZONE, assessment_status VARCHAR(24) NOT NULL DEFAULT 'pending',
        assessment_attempts INTEGER NOT NULL DEFAULT 0, last_assessment_error TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_events_recent_v2 ON news_events_v2(last_seen)",
    """CREATE TABLE IF NOT EXISTS event_articles_v2 (
        event_id VARCHAR(36) NOT NULL, article_id VARCHAR(32) NOT NULL,
        linked_at TIMESTAMP WITH TIME ZONE NOT NULL, link_method VARCHAR(24) NOT NULL,
        link_confidence DOUBLE PRECISION NOT NULL,
        PRIMARY KEY(event_id,article_id)
    )""",
    """CREATE TABLE IF NOT EXISTS ai_decisions_v2 (
        id VARCHAR(36) PRIMARY KEY, decision_type VARCHAR(32) NOT NULL,
        subject_id TEXT NOT NULL, input_json TEXT NOT NULL, output_json TEXT NOT NULL,
        model TEXT NOT NULL, created_at TIMESTAMP WITH TIME ZONE NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS settings (
        key VARCHAR(120) PRIMARY KEY, value TEXT NOT NULL, updated_at TIMESTAMP WITH TIME ZONE NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS deliveries (
        id VARCHAR(100) PRIMARY KEY, kind VARCHAR(24) NOT NULL,
        event_fingerprint VARCHAR(40), delivered_at TIMESTAMP WITH TIME ZONE NOT NULL,
        target_type VARCHAR(16) NOT NULL, target_openid TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS digest_event_history_v3 (
        edition_id VARCHAR(80) NOT NULL, kind VARCHAR(16) NOT NULL, event_id VARCHAR(36) NOT NULL,
        snapshot_json TEXT NOT NULL, included_at TIMESTAMP WITH TIME ZONE NOT NULL,
        substantive_update BOOLEAN NOT NULL, PRIMARY KEY(edition_id,event_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_digest_event_v3 ON digest_event_history_v3(event_id,included_at)",
    """CREATE TABLE IF NOT EXISTS ai_failures_v3 (
        id VARCHAR(36) PRIMARY KEY, work_type VARCHAR(32) NOT NULL, subject_id TEXT NOT NULL,
        error TEXT NOT NULL, retryable BOOLEAN NOT NULL, created_at TIMESTAMP WITH TIME ZONE NOT NULL
    )""",
]


class Database:
    def __init__(self, url: str):
        self.engine = create_engine(url, pool_pre_ping=True)

    def initialize(self) -> None:
        with self.engine.begin() as connection:
            for statement in SCHEMA:
                connection.execute(text(statement))
            # V0.2 databases are upgraded in place; QQ settings and delivery history remain untouched.
            if self.engine.dialect.name == "sqlite":
                event_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(news_events_v2)"))}
                event_additions = {
                    "assessment_status": "VARCHAR(24) NOT NULL DEFAULT 'pending'",
                    "assessment_attempts": "INTEGER NOT NULL DEFAULT 0",
                    "last_assessment_error": "TEXT",
                }
                for name, definition in event_additions.items():
                    if name not in event_columns:
                        connection.execute(text(f"ALTER TABLE news_events_v2 ADD COLUMN {name} {definition}"))
                article_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(article_versions_v2)"))}
                article_additions = {
                    "embedding": "TEXT",
                    "embedding_model": "TEXT",
                    "embedding_dimensions": "INTEGER",
                    "embedded_at": "TIMESTAMP WITH TIME ZONE",
                    "source_tier": "VARCHAR(24) NOT NULL DEFAULT 'standard'",
                    "primary_source": "BOOLEAN NOT NULL DEFAULT false",
                    "default_weight": "DOUBLE PRECISION NOT NULL DEFAULT 1.0",
                }
                for name, definition in article_additions.items():
                    if name not in article_columns:
                        connection.execute(text(f"ALTER TABLE article_versions_v2 ADD COLUMN {name} {definition}"))

    def set_setting(self, key: str, value: object) -> None:
        encoded, now = json.dumps(value, ensure_ascii=False), datetime.now(timezone.utc)
        with self.engine.begin() as connection:
            connection.execute(text("DELETE FROM settings WHERE key=:key"), {"key": key})
            connection.execute(text("INSERT INTO settings(key,value,updated_at) VALUES(:key,:value,:now)"),
                               {"key": key, "value": encoded, "now": now})

    def get_setting(self, key: str, default=None):
        with self.engine.connect() as connection:
            row = connection.execute(text("SELECT value FROM settings WHERE key=:key"), {"key": key}).first()
        return json.loads(row[0]) if row else default

    def ingest_articles(self, articles: Iterable[Article]) -> IngestResult:
        result = IngestResult()
        with self.engine.begin() as connection:
            for article in articles:
                exact = connection.execute(text("""SELECT 1 FROM article_versions_v2
                    WHERE id=:id OR (source_id=:source_id AND content_hash=:content_hash)"""), {
                    "id": article.id, "source_id": article.source_id, "content_hash": article.content_hash,
                }).first()
                if exact:
                    result.exact_duplicates += 1
                    continue
                latest = connection.execute(text("""SELECT id,version_no FROM article_versions_v2
                    WHERE source_id=:source_id AND family_key=:family_key AND is_latest=true
                    ORDER BY version_no DESC LIMIT 1"""), {
                    "source_id": article.source_id, "family_key": article.family_key,
                }).first()
                version_no = (latest.version_no + 1) if latest else 1
                if latest:
                    connection.execute(text("UPDATE article_versions_v2 SET is_latest=false WHERE id=:id"), {"id": latest.id})
                    result.new_versions += 1
                else:
                    result.new_families += 1
                connection.execute(text("""INSERT INTO article_versions_v2
                    (id,source_id,source_name,source_group,source_kind,official,source_tier,primary_source,
                    default_weight,external_id,original_url,
                    canonical_url,content_hash,family_key,title,summary,category,region,published_at,
                    discovered_at,version_no,is_latest,cluster_status)
                    VALUES(:id,:source_id,:source_name,:source_group,:source_kind,:official,:source_tier,
                    :primary_source,:default_weight,:external_id,:url,
                    :canonical_url,:content_hash,:family_key,:title,:summary,:category,:region,:published_at,
                    :discovered_at,:version_no,true,'exact_deduped')"""), {
                    "id": article.id, "source_id": article.source_id, "source_name": article.source_name,
                    "source_group": article.source_group, "source_kind": article.source_kind,
                    "official": article.official, "external_id": article.external_id, "url": article.url,
                    "source_tier": article.source_tier, "primary_source": article.primary_source,
                    "default_weight": article.default_weight,
                    "canonical_url": article.canonical_url, "content_hash": article.content_hash,
                    "family_key": article.family_key, "title": article.title, "summary": article.summary,
                    "category": article.category, "region": article.region,
                    "published_at": article.published_at, "discovered_at": article.discovered_at,
                    "version_no": version_no,
                })
                result.inserted_ids.append(article.id)
        return result

    def pending_articles(self, limit: int = 500) -> list[dict]:
        with self.engine.connect() as connection:
            rows = connection.execute(text("""SELECT * FROM article_versions_v2
                WHERE is_latest=true AND cluster_status IN ('pending','exact_deduped','embedded')
                ORDER BY published_at ASC LIMIT :limit"""), {"limit": limit}).mappings().all()
        items = [self._coerce_times(dict(row)) for row in rows]
        for item in items:
            raw = item.get("embedding")
            item["embedding"] = json.loads(raw) if raw else None
        return items

    def save_article_embeddings(self, rows: list[tuple[str, list[float]]], model: str) -> None:
        """Persist one successful provider batch as its own checkpoint transaction."""
        if not rows:
            return
        now = datetime.now(timezone.utc)
        with self.engine.begin() as connection:
            for article_id, vector in rows:
                connection.execute(text("""UPDATE article_versions_v2
                    SET embedding=:embedding,embedding_model=:model,embedding_dimensions=:dimensions,
                        embedded_at=:at,cluster_status='embedded'
                    WHERE id=:id AND cluster_status IN ('pending','exact_deduped','embedded')"""), {
                    "id": article_id, "embedding": json.dumps(vector), "model": model,
                    "dimensions": len(vector), "at": now,
                })

    def pending_counts(self) -> dict[str, int]:
        with self.engine.connect() as connection:
            articles = connection.execute(text("""SELECT COUNT(*) FROM article_versions_v2
                WHERE is_latest=true AND cluster_status IN ('pending','exact_deduped','embedded')""")).scalar_one()
            events = connection.execute(text("""SELECT COUNT(*) FROM news_events_v2
                WHERE assessment_status IN ('pending','retry')""")).scalar_one()
        return {
            "article_pipeline": int(articles),
            "event_assessment": int(events),
            "total": int(articles) + int(events),
        }

    def pending_work_count(self) -> int:
        """Backward-compatible total; logs should use pending_counts() for an explicit breakdown."""
        return self.pending_counts()["total"]

    def event_for_family(self, source_id: str, family_key: str) -> str | None:
        with self.engine.connect() as connection:
            row = connection.execute(text("""SELECT ea.event_id FROM event_articles_v2 ea
                JOIN article_versions_v2 a ON a.id=ea.article_id
                WHERE a.source_id=:source_id AND a.family_key=:family_key
                ORDER BY a.version_no DESC LIMIT 1"""),
                {"source_id": source_id, "family_key": family_key}).first()
        return row[0] if row else None

    def recent_events(self, lookback_hours: int, limit: int = 2000) -> list[dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        with self.engine.connect() as connection:
            rows = connection.execute(text("""SELECT * FROM news_events_v2
                WHERE last_seen>=:cutoff ORDER BY last_seen DESC LIMIT :limit"""),
                {"cutoff": cutoff, "limit": limit}).mappings().all()
        return [self._event_row(row) for row in rows]

    @staticmethod
    def _event_row(row) -> dict:
        item = Database._coerce_times(dict(row))
        item["embedding"] = json.loads(item.get("embedding") or "[]")
        return item

    @staticmethod
    def _coerce_times(item: dict) -> dict:
        for key in ("published_at", "discovered_at", "embedded_at", "first_seen", "last_seen", "assessed_at"):
            value = item.get(key)
            if isinstance(value, str):
                item[key] = datetime.fromisoformat(value.replace("Z", "+00:00"))
            elif isinstance(value, datetime) and value.tzinfo is None:
                item[key] = value.replace(tzinfo=timezone.utc)
        return item

    def create_event(self, article: dict, embedding: list[float]) -> str:
        event_id = str(uuid4())
        with self.engine.begin() as connection:
            connection.execute(text("""INSERT INTO news_events_v2
                (id,canonical_title,summary,category,region,first_seen,last_seen,embedding,article_count,
                independent_source_count,official_source_count,velocity_30m,status)
                VALUES(:id,:title,:summary,:category,:region,:first,:last,:embedding,0,0,0,0,'developing')"""), {
                "id": event_id, "title": article["title"], "summary": article["summary"],
                "category": article["category"], "region": article["region"],
                "first": article["published_at"], "last": article["published_at"],
                "embedding": json.dumps(embedding),
            })
        return event_id

    def attach_article(self, event_id: str, article_id: str, method: str, confidence: float) -> None:
        with self.engine.begin() as connection:
            linked = connection.execute(text("SELECT 1 FROM event_articles_v2 WHERE event_id=:eid AND article_id=:aid"),
                                        {"eid": event_id, "aid": article_id}).first()
            if not linked:
                connection.execute(text("""INSERT INTO event_articles_v2
                    (event_id,article_id,linked_at,link_method,link_confidence)
                    VALUES(:eid,:aid,:at,:method,:confidence)"""), {
                    "eid": event_id, "aid": article_id, "at": datetime.now(timezone.utc),
                    "method": method, "confidence": confidence,
                })
            connection.execute(text("UPDATE article_versions_v2 SET cluster_status='clustered' WHERE id=:aid"), {"aid": article_id})
            connection.execute(text("""UPDATE news_events_v2 SET assessment_status='pending',
                last_assessment_error=NULL WHERE id=:eid"""), {"eid": event_id})
        self.refresh_event_evidence(event_id)

    def refresh_event_evidence(self, event_id: str) -> dict:
        now, cutoff = datetime.now(timezone.utc), datetime.now(timezone.utc) - timedelta(minutes=30)
        # Only the four columns the evidence below actually reads are selected. The embedding
        # group is ~94% of an article row and this query re-reads *every* article of the event
        # on each attach, so a SELECT * here is the single biggest source of network transfer.
        # Keep this list in sync with the fields used below.
        query = text("""SELECT a.source_group, a.official, a.discovered_at, a.published_at
            FROM article_versions_v2 a
            JOIN event_articles_v2 ea ON ea.article_id=a.id WHERE ea.event_id=:eid""")
        with self.engine.begin() as connection:
            articles = [self._coerce_times(dict(row)) for row in connection.execute(query, {"eid": event_id}).mappings().all()]
            groups = {article["source_group"] for article in articles}
            officials = {article["source_group"] for article in articles if article["official"]}
            velocity = sum(article["discovered_at"] >= cutoff for article in articles)
            first = min(article["published_at"] for article in articles)
            last = max(article["published_at"] for article in articles)
            connection.execute(text("""UPDATE news_events_v2 SET article_count=:count,
                independent_source_count=:sources,official_source_count=:officials,velocity_30m=:velocity,
                first_seen=:first,last_seen=:last WHERE id=:eid"""), {
                "count": len(articles), "sources": len(groups), "officials": len(officials),
                "velocity": velocity, "first": first, "last": last, "eid": event_id,
            })
        return {"article_count": len(articles), "independent_source_count": len(groups),
                "official_source_count": len(officials), "velocity_30m": velocity,
                "age_minutes": max(0, int((now - first).total_seconds() / 60))}

    def update_event_embedding(self, event_id: str, embedding: list[float]) -> None:
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE news_events_v2 SET embedding=:embedding WHERE id=:eid"),
                               {"embedding": json.dumps(embedding), "eid": event_id})

    def event_payload(self, event_id: str) -> dict:
        # Payload consumers (importance evidence, digest editor, breaking gate, digest snapshot)
        # read text, timestamps and counters only -- never a vector. The embedding group is
        # 94-99% of a row, so it is left out here. Keep both column lists in sync with callers:
        # a missing column surfaces as a default value, not as an error.
        with self.engine.connect() as connection:
            event = connection.execute(text("""SELECT id, canonical_title, summary, category, region,
                first_seen, last_seen, article_count, independent_source_count, official_source_count,
                velocity_30m, importance, breaking, scope, confidence, importance_reason, status,
                assessed_at, assessment_status, assessment_attempts, last_assessment_error
                FROM news_events_v2 WHERE id=:eid"""), {"eid": event_id}).mappings().first()
            rows = connection.execute(text("""SELECT a.id, a.source_id, a.source_name, a.source_group,
                a.source_kind, a.source_tier, a.official, a.primary_source, a.default_weight,
                a.external_id, a.original_url, a.canonical_url, a.content_hash, a.family_key,
                a.title, a.summary, a.category, a.region, a.published_at, a.discovered_at,
                a.version_no, a.is_latest, a.cluster_status
                FROM article_versions_v2 a
                JOIN event_articles_v2 ea ON ea.article_id=a.id WHERE ea.event_id=:eid
                ORDER BY a.published_at ASC"""), {"eid": event_id}).mappings().all()
        if not event:
            raise KeyError(event_id)
        payload = self._event_row(event)
        payload["articles"] = [self._coerce_times(dict(row)) for row in rows]
        return payload

    def update_assessment(self, event_id: str, assessment: dict) -> None:
        status = "background" if assessment["stale_or_background"] else ("confirmed" if assessment["confidence"] >= .8 else "developing")
        with self.engine.begin() as connection:
            connection.execute(text("""UPDATE news_events_v2 SET canonical_title=:canonical_title,summary=:summary,
                category=:category,region=:region,importance=:importance,breaking=:breaking,
                scope=:scope,confidence=:confidence,importance_reason=:reason,status=:status,assessed_at=:at,
                assessment_status='done',last_assessment_error=NULL
                WHERE id=:eid"""), {**assessment, "eid": event_id, "status": status, "at": datetime.now(timezone.utc)})

    def mark_assessment_retry(self, event_id: str, error: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(text("""UPDATE news_events_v2 SET assessment_status='retry',
                assessment_attempts=assessment_attempts+1,last_assessment_error=:error WHERE id=:eid"""),
                {"eid": event_id, "error": error[:1000]})

    def pending_assessment_event_ids(self, limit: int = 300) -> list[str]:
        with self.engine.connect() as connection:
            rows = connection.execute(text("""SELECT id FROM news_events_v2
                WHERE assessment_status IN ('pending','retry') ORDER BY last_seen ASC LIMIT :limit"""),
                {"limit": limit}).all()
        return [row[0] for row in rows]

    def record_ai_failure(self, work_type: str, subject_id: str, error: str, retryable: bool = True) -> None:
        with self.engine.begin() as connection:
            connection.execute(text("""INSERT INTO ai_failures_v3
                (id,work_type,subject_id,error,retryable,created_at)
                VALUES(:id,:type,:subject,:error,:retryable,:at)"""), {
                "id": str(uuid4()), "type": work_type, "subject": subject_id,
                "error": error[:2000], "retryable": retryable, "at": datetime.now(timezone.utc),
            })

    def record_ai_decision(self, decision_type: str, subject_id: str, input_data: dict, output_data: dict, model: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(text("""INSERT INTO ai_decisions_v2
                (id,decision_type,subject_id,input_json,output_json,model,created_at)
                VALUES(:id,:type,:subject,:input,:output,:model,:at)"""), {
                "id": str(uuid4()), "type": decision_type, "subject": subject_id,
                "input": json.dumps(input_data, ensure_ascii=False, default=str),
                "output": json.dumps(output_data, ensure_ascii=False, default=str),
                "model": model, "at": datetime.now(timezone.utc),
            })

    def events_between(self, start: datetime, end: datetime, limit: int = 100) -> list[dict]:
        with self.engine.connect() as connection:
            ids = [row[0] for row in connection.execute(text("""SELECT id FROM news_events_v2
                WHERE last_seen>=:start AND last_seen<:end AND assessment_status='done'
                AND importance IS NOT NULL AND status<>'background'
                ORDER BY importance DESC,last_seen DESC LIMIT :limit"""),
                {"start": start, "end": end, "limit": limit}).all()]
        return [self.event_payload(event_id) for event_id in ids]

    def breaking_event_pool(self, start: datetime) -> list[dict]:
        with self.engine.connect() as connection:
            ids = [row[0] for row in connection.execute(text("""SELECT id FROM news_events_v2
                WHERE last_seen>=:start AND assessment_status='done' AND breaking=true
                ORDER BY importance DESC,last_seen DESC"""),
                {"start": start}).all()]
        return [self.event_payload(event_id) for event_id in ids]

    def was_event_breaking_delivered(self, event_id: str) -> bool:
        with self.engine.connect() as connection:
            return connection.execute(text("""SELECT 1 FROM deliveries
                WHERE kind='breaking' AND event_fingerprint=:eid LIMIT 1"""), {"eid": event_id}).first() is not None

    def was_delivered(self, delivery_id: str) -> bool:
        with self.engine.connect() as connection:
            return connection.execute(text("SELECT 1 FROM deliveries WHERE id=:id"), {"id": delivery_id}).first() is not None

    def mark_delivered(self, delivery_id: str, kind: str, target: dict, event_fingerprint: str | None = None) -> None:
        with self.engine.begin() as connection:
            connection.execute(text("""INSERT INTO deliveries
                (id,kind,event_fingerprint,delivered_at,target_type,target_openid)
                VALUES(:id,:kind,:fp,:at,:type,:openid)"""), {
                "id": delivery_id, "kind": kind, "fp": event_fingerprint,
                "at": datetime.now(timezone.utc), "type": target["type"], "openid": target["openid"],
            })

    def latest_digest_snapshot(self, event_id: str) -> dict | None:
        with self.engine.connect() as connection:
            row = connection.execute(text("""SELECT snapshot_json FROM digest_event_history_v3
                WHERE event_id=:eid ORDER BY included_at DESC LIMIT 1"""), {"eid": event_id}).first()
        return json.loads(row[0]) if row else None

    def record_digest_events(self, edition_id: str, kind: str, events: list[dict]) -> None:
        now = datetime.now(timezone.utc)
        with self.engine.begin() as connection:
            for event in events:
                snapshot = self.digest_snapshot(event)
                connection.execute(text("""INSERT INTO digest_event_history_v3
                    (edition_id,kind,event_id,snapshot_json,included_at,substantive_update)
                    VALUES(:edition,:kind,:eid,:snapshot,:at,:update)"""), {
                    "edition": edition_id, "kind": kind, "eid": event["id"],
                    "snapshot": json.dumps(snapshot, ensure_ascii=False, default=str), "at": now,
                    "update": bool(event.get("is_update")),
                })

    @staticmethod
    def digest_snapshot(event: dict) -> dict:
        articles = event.get("articles", [])
        return {
            "event_id": event["id"], "title": event["canonical_title"], "summary": event["summary"],
            "status": event.get("status"), "importance": event.get("importance"),
            "confidence": event.get("confidence"), "last_seen": event.get("last_seen"),
            "independent_source_count": event.get("independent_source_count", 0),
            "official_source_count": event.get("official_source_count", 0),
            "article_ids": [item["id"] for item in articles],
            "reports": [{"source": item["source_name"], "title": item["title"],
                         "published_at": item["published_at"]} for item in articles[-20:]],
        }
