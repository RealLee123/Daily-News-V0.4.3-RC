from __future__ import annotations


def is_breaking_candidate(event: dict, settings) -> bool:
    """The single final reliability gate for reports, jobs, and future delivery code."""
    return bool(
        event.get("breaking")
        and (event.get("importance") or 0) >= getattr(settings, "breaking_threshold", 80)
        and (event.get("confidence") or 0.0) >= getattr(settings, "breaking_min_confidence", 0.8)
        and (
            event.get("independent_source_count", 0) >= getattr(settings, "breaking_min_sources", 2)
            or event.get("official_source_count", 0) >= 1
        )
        and event.get("status") != "background"
    )
