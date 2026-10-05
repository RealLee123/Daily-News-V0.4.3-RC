from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for raw in path.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    qq_app_id: str = os.getenv("QQ_APP_ID", "")
    qq_app_secret: str = os.getenv("QQ_APP_SECRET", "")
    qq_target_type: str = os.getenv("QQ_TARGET_TYPE", "c2c")
    # PostgreSQL (Neon) is the production database. There is deliberately no SQLite
    # fallback: an empty value is reported instead of silently writing somewhere else.
    database_url: str = os.getenv("DATABASE_URL", "")
    ai_provider: str = os.getenv("AI_PROVIDER", "gemini").lower()
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    gemini_embedding_model: str = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
    ai_timeout: float = float(os.getenv("AI_TIMEOUT_SECONDS", "60"))
    ai_min_call_interval: float = float(os.getenv("AI_MIN_CALL_INTERVAL_SECONDS", "6"))
    ai_max_assessments_per_run: int = int(os.getenv("AI_MAX_ASSESSMENTS_PER_RUN", "12"))
    ai_max_cluster_reviews_per_run: int = int(os.getenv("AI_MAX_CLUSTER_REVIEWS_PER_RUN", "12"))
    embedding_dimensions: int = int(os.getenv("EMBEDDING_DIMENSIONS", "512"))
    event_auto_merge_threshold: float = float(os.getenv("EVENT_AUTO_MERGE_THRESHOLD", "0.88"))
    event_llm_review_threshold: float = float(os.getenv("EVENT_LLM_REVIEW_THRESHOLD", "0.62"))
    event_candidate_limit: int = int(os.getenv("EVENT_CANDIDATE_LIMIT", "5"))
    event_lookback_hours: int = int(os.getenv("EVENT_LOOKBACK_HOURS", "96"))
    timezone: str = os.getenv("TIMEZONE", "Asia/Shanghai")
    morning_time: str = os.getenv("MORNING_TIME", "09:30")
    evening_time: str = os.getenv("EVENING_TIME", "21:30")
    breaking_threshold: int = int(os.getenv("BREAKING_THRESHOLD", "80"))
    breaking_min_sources: int = int(os.getenv("BREAKING_MIN_SOURCES", "2"))
    breaking_min_confidence: float = float(os.getenv("BREAKING_MIN_CONFIDENCE", "0.80"))
    # The scan runs every two hours, so the alert lookback must be longer than that:
    # an event has to survive several runs before a quiet one can take its turn.
    breaking_pool_hours: int = int(os.getenv("BREAKING_POOL_HOURS", "6"))
    breaking_max_alerts_per_run: int = int(os.getenv("BREAKING_MAX_ALERTS_PER_RUN", "3"))
    max_digest_events: int = int(os.getenv("MAX_DIGEST_EVENTS", "24"))
    qq_message_max_chars: int = int(os.getenv("QQ_MESSAGE_MAX_CHARS", "1800"))
    qq_preview_hours: int = int(os.getenv("QQ_PREVIEW_HOURS", "168"))
    fetch_timeout: float = float(os.getenv("FETCH_TIMEOUT_SECONDS", "20"))
    reuters_enabled: bool = _env_bool("REUTERS_ENABLED", True)
    bloomberg_enabled: bool = _env_bool("BLOOMBERG_ENABLED", True)
    white_house_enabled: bool = _env_bool("WHITE_HOUSE_ENABLED", True)
    trump_x_enabled: bool = _env_bool("TRUMP_X_ENABLED", False)
    x_bearer_token: str = os.getenv("X_BEARER_TOKEN", "")
    sources_file: Path = ROOT / "config" / "sources.json"
    output_dir: Path = ROOT / "output"

    @property
    def ai_enabled(self) -> bool:
        return self.ai_provider == "gemini" and bool(self.gemini_api_key)

    @property
    def ai_model_name(self) -> str:
        if self.ai_provider == "gemini":
            return self.gemini_model
        return ""

    def require_database_url(self) -> str:
        """The production database is PostgreSQL, so refuse to guess a fallback."""
        if not self.database_url.strip():
            raise RuntimeError(
                "未配置 DATABASE_URL（生产库是 PostgreSQL）。请先运行：\n"
                "  python -m daily_news setup-postgres\n"
                "只在本地用 SQLite 时显式指定，例如：--database-url sqlite:///news.db"
            )
        return self.database_url

    def require_ai(self) -> None:
        if self.ai_provider != "gemini":
            raise RuntimeError(f"尚未安装 AI Provider：{self.ai_provider}（当前支持 gemini）")
        if not self.gemini_api_key:
            raise RuntimeError("缺少 GEMINI_API_KEY：请复制 .env.example 为 .env 后填写")

    def require_qq(self) -> None:
        missing = [name for name, value in (("QQ_APP_ID", self.qq_app_id), ("QQ_APP_SECRET", self.qq_app_secret)) if not value]
        if missing:
            raise RuntimeError(f"缺少配置：{', '.join(missing)}")
        if self.qq_target_type not in {"c2c", "group"}:
            raise RuntimeError("QQ_TARGET_TYPE 只能是 c2c 或 group")
