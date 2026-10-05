from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256


@dataclass(slots=True)
class Article:
    source_id: str
    source_name: str
    source_group: str
    source_kind: str
    official: bool
    title: str
    url: str
    canonical_url: str
    external_id: str
    content_hash: str
    family_key: str
    category: str
    region: str
    published_at: datetime
    source_tier: str = "standard"
    primary_source: bool = False
    default_weight: float = 1.0
    summary: str = ""
    discovered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    id: str = field(init=False)

    def __post_init__(self) -> None:
        identity = f"{self.source_id}|{self.family_key}|{self.content_hash}"
        self.id = sha256(identity.encode()).hexdigest()[:32]
        if self.published_at.tzinfo is None:
            self.published_at = self.published_at.replace(tzinfo=timezone.utc)


@dataclass(slots=True)
class Event:
    id: str
    canonical_title: str
    summary: str
    category: str
    region: str
    first_seen: datetime
    last_seen: datetime
    embedding: list[float]
    article_count: int = 0
    independent_source_count: int = 0
    official_source_count: int = 0
    velocity_30m: int = 0
    importance: int | None = None
    breaking: bool | None = None
    scope: str | None = None
    confidence: float | None = None
    importance_reason: str | None = None
    status: str = "developing"


@dataclass(slots=True)
class IngestResult:
    inserted_ids: list[str] = field(default_factory=list)
    exact_duplicates: int = 0
    new_families: int = 0
    new_versions: int = 0
