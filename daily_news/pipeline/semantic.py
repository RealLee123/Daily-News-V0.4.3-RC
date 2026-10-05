from __future__ import annotations

import math


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(value * value for value in left))
    norm_right = math.sqrt(sum(value * value for value in right))
    return dot / (norm_left * norm_right) if norm_left and norm_right else 0.0


def event_text(title: str, summary: str, region: str, category: str) -> str:
    return f"Title: {title}\nSummary: {summary[:1600]}\nRegion: {region}\nDesk: {category}"


def weighted_average(old: list[float], new: list[float], old_weight: int) -> list[float]:
    if not old:
        return new
    total = max(1, old_weight) + 1
    return [((a * max(1, old_weight)) + b) / total for a, b in zip(old, new)]
