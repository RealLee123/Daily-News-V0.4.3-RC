from __future__ import annotations

from daily_news.ai.base import AIProvider


def create_ai_provider(settings) -> AIProvider:
    if settings.ai_provider == "gemini":
        from daily_news.ai.gemini import GeminiProvider

        return GeminiProvider(settings)
    raise RuntimeError(f"未知 AI Provider：{settings.ai_provider}。当前实现：gemini")
