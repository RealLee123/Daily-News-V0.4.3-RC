import json
from types import SimpleNamespace

import pytest

from daily_news.ai import AIConfigurationError, AIRetryableError
from daily_news.ai.base import AIProvider
from daily_news.ai.gemini import GeminiProvider
from daily_news.config import Settings


def test_gemini_timeout_is_retryable_and_counted():
    provider = GeminiProvider.__new__(GeminiProvider)
    AIProvider.__init__(provider)
    provider.settings = SimpleNamespace(
        ai_min_call_interval=0,
        gemini_model="gemini-3.5-flash-lite",
        gemini_embedding_model="gemini-embedding-001",
    )
    provider.model_name = provider.settings.gemini_model

    def fail():
        raise TimeoutError("request timed out")

    with pytest.raises(AIRetryableError):
        provider._run("importance", fail)
    assert provider.usage.calls == 1
    assert provider.usage.failures == 1
    assert provider.usage.importance_calls == 1


def test_default_text_and_embedding_models_are_independent():
    fields = Settings.__dataclass_fields__
    assert fields["gemini_model"].default == "gemini-3.5-flash-lite"
    assert fields["gemini_embedding_model"].default == "gemini-embedding-001"


def test_model_404_is_permanent_and_has_friendly_context():
    class NotFoundError(Exception):
        status_code = 404

    provider = GeminiProvider.__new__(GeminiProvider)
    AIProvider.__init__(provider)
    provider.provider_name = "gemini"
    provider.settings = SimpleNamespace(
        ai_min_call_interval=0,
        gemini_model="gemini-3.5-flash-lite",
        gemini_embedding_model="gemini-embedding-001",
    )
    provider.model_name = provider.settings.gemini_model

    with pytest.raises(AIConfigurationError) as caught:
        provider._run("importance", lambda: (_ for _ in ()).throw(NotFoundError("no longer available")))
    message = str(caught.value)
    assert "Provider：gemini" in message
    assert "模型：gemini-3.5-flash-lite" in message
    assert "HTTP 404" in message
    assert "GEMINI_MODEL" in message


def test_source_registry_has_explicit_metadata_and_trump_x_defaults_off():
    from daily_news.config import ROOT

    sources = json.loads((ROOT / "config" / "sources.json").read_text("utf-8"))
    required = {"source_id", "source_name", "source_type", "source_tier", "source_group",
                "official", "primary_source", "default_weight", "fetch_method", "enabled"}
    assert all(required <= item.keys() for item in sources)
    by_id = {item["source_id"]: item for item in sources}
    assert by_id["reuters"]["fetch_method"] == "news_sitemap"
    assert by_id["bloomberg"]["fetch_method"] == "bloomberg_latest_sitemap"
    assert by_id["donald-trump-x"]["fetch_method"] == "x_api_v2"
    assert by_id["donald-trump-x"]["enabled"] is False
