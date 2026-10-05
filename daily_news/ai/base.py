from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Callable


class AIProviderError(RuntimeError):
    """Base exception raised by an AI provider adapter."""

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        model: str = "",
        error_type: str = "AI API 错误",
        suggestion: str = "请检查配置后重试",
    ) -> None:
        self.detail = message
        self.provider = provider
        self.model = model
        self.error_type = error_type
        self.suggestion = suggestion
        lines = [
            f"Provider：{provider or 'unknown'}",
            f"模型：{model or 'unknown'}",
            f"错误类型：{error_type}",
            f"建议：{suggestion}",
        ]
        if message:
            lines.append(f"详情：{message}")
        super().__init__("\n".join(lines))


class AIRetryableError(AIProviderError):
    """Temporary/quota failure. Database work must remain pending."""


class AIConfigurationError(AIProviderError):
    """Permanent model/key/configuration failure; retry only after configuration changes."""


class AIResponseError(AIProviderError):
    """The provider returned an unusable response."""


@dataclass
class AIUsage:
    calls: int = 0
    failures: int = 0
    retries: int = 0
    embedding_calls: int = 0
    clustering_calls: int = 0
    importance_calls: int = 0
    update_calls: int = 0
    editing_calls: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class AIProvider(ABC):
    """Provider-neutral contract used by the news pipeline."""

    provider_name = "abstract"
    model_name = ""
    embedding_model_name = ""

    def __init__(self) -> None:
        self.usage = AIUsage()

    @property
    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def embeddings(
        self,
        texts: list[str],
        on_batch: Callable[[int, list[list[float]]], None] | None = None,
    ) -> list[list[float]]: ...

    @abstractmethod
    def decide_event_match(self, article: dict, candidates: list[dict]) -> dict: ...

    @abstractmethod
    def assess_importance(self, evidence: dict) -> dict: ...

    @abstractmethod
    def assess_substantive_update(self, previous: dict, current: dict) -> dict: ...

    @abstractmethod
    def edit_digest(self, edition: str, events: list[dict]) -> str: ...
