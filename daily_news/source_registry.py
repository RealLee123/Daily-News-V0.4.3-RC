from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


SOURCE_TYPES = {"news_media", "official", "social_primary", "aggregator"}


@dataclass(frozen=True, slots=True)
class SourceDefinition:
    source_id: str
    source_name: str
    source_type: str
    source_tier: str
    source_group: str
    official: bool
    primary_source: bool
    default_weight: float
    fetch_method: str
    enabled: bool
    region: str
    category: str
    url: str = ""
    options: dict | None = None

    @classmethod
    def from_dict(cls, raw: dict, settings=None) -> "SourceDefinition":
        source_type = raw.get("source_type", raw.get("kind", "news_media"))
        source_type = {"media": "news_media", "wire": "news_media", "discovery": "aggregator"}.get(
            source_type, source_type
        )
        if source_type not in SOURCE_TYPES:
            raise ValueError(f"unsupported source_type for {raw.get('source_id', raw.get('id'))}: {source_type}")
        enabled = bool(raw.get("enabled", False))
        setting_name = raw.get("enabled_setting")
        if setting_name and settings is not None:
            enabled = bool(getattr(settings, setting_name, enabled))
        source_id = raw.get("source_id") or raw.get("id")
        source_name = raw.get("source_name") or raw.get("name")
        if not source_id or not source_name:
            raise ValueError("source_id/source_name are required")
        return cls(
            source_id=source_id,
            source_name=source_name,
            source_type=source_type,
            source_tier=raw.get("source_tier", "standard"),
            source_group=raw.get("source_group") or raw.get("group") or source_name,
            official=bool(raw.get("official", False)),
            primary_source=bool(raw.get("primary_source", False)),
            default_weight=float(raw.get("default_weight", 1.0)),
            fetch_method=raw.get("fetch_method", raw.get("collector", "rss")),
            enabled=enabled,
            region=raw.get("region", "Global"),
            category=raw.get("category", "world"),
            url=raw.get("url", ""),
            options=raw.get("options", {}),
        )

    def article_metadata(self) -> dict:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "source_group": self.source_group,
            "source_kind": self.source_type,
            "source_tier": self.source_tier,
            "official": self.official,
            "primary_source": self.primary_source,
            "default_weight": self.default_weight,
            "category": self.category,
            "region": self.region,
        }


class SourceRegistry:
    def __init__(self, sources: list[SourceDefinition]):
        self.sources = sources

    @classmethod
    def load(cls, path: Path, settings=None) -> "SourceRegistry":
        raw_sources = json.loads(path.read_text("utf-8"))
        return cls([SourceDefinition.from_dict(item, settings) for item in raw_sources])

    def enabled(self) -> list[SourceDefinition]:
        return [source for source in self.sources if source.enabled]
