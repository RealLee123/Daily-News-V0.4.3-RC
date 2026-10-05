from __future__ import annotations

import json
import time
from typing import Callable

from daily_news.ai.base import AIConfigurationError, AIProvider, AIProviderError, AIResponseError, AIRetryableError


MATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "match": {"type": "boolean"},
        "event_id": {"type": "string", "description": "不匹配时返回空字符串"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
    },
    "required": ["match", "event_id", "confidence", "reason"],
    "additionalProperties": False,
}

IMPORTANCE_SCHEMA = {
    "type": "object",
    "properties": {
        "canonical_title": {"type": "string"},
        "summary": {"type": "string"},
        "category": {"type": "string", "enum": ["politics", "diplomacy", "economy", "markets", "military", "technology", "science", "climate", "disaster", "society", "culture", "other"]},
        "region": {"type": "string"},
        "importance": {"type": "integer", "minimum": 0, "maximum": 100},
        "breaking": {"type": "boolean"},
        "scope": {"type": "string", "enum": ["local", "national", "regional", "international", "global"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
        "stale_or_background": {"type": "boolean"},
        "needs_more_sources": {"type": "boolean"},
    },
    "required": ["canonical_title", "summary", "category", "region", "importance", "breaking", "scope", "confidence", "reason", "stale_or_background", "needs_more_sources"],
    "additionalProperties": False,
}

UPDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "substantive": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "update_label": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["substantive", "confidence", "update_label", "reason"],
    "additionalProperties": False,
}


class GeminiProvider(AIProvider):
    provider_name = "gemini"

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.model_name = settings.gemini_model
        self.embedding_model_name = settings.gemini_embedding_model
        self._client = None
        self._types = None
        self._last_call_at = 0.0
        if settings.gemini_api_key:
            from google import genai
            from google.genai import types

            self._types = types
            self._client = genai.Client(
                api_key=settings.gemini_api_key,
                http_options=types.HttpOptions(timeout=int(settings.ai_timeout * 1000)),
            )

    @property
    def available(self) -> bool:
        return self._client is not None

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        text = f"{type(exc).__name__}: {exc}".lower()
        return code in {408, 429, 500, 502, 503, 504} or any(token in text for token in (
            "resource_exhausted", "quota", "rate limit", "too many requests", "timeout",
            "timed out", "deadline", "temporarily", "unavailable", "connection",
            "ssl", "eof", "reset", "broken pipe", "disconnect", "handshake", "econnreset",
        ))

    @staticmethod
    def _status_code(exc: Exception) -> int | None:
        code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        if callable(code):
            code = code()
        try:
            return int(code)
        except (TypeError, ValueError):
            return None

    def _active_model(self, kind: str) -> str:
        return self.settings.gemini_embedding_model if kind == "embedding" else self.model_name

    def _classified_error(self, kind: str, exc: Exception) -> AIProviderError:
        model = self._active_model(kind)
        detail = getattr(exc, "detail", None) or str(exc)
        code = self._status_code(exc)
        lower = f"{type(exc).__name__}: {detail}".lower()
        common = {"provider": self.provider_name, "model": model}

        if code == 404 or "not found" in lower or "no longer available" in lower:
            setting = "GEMINI_EMBEDDING_MODEL" if kind == "embedding" else "GEMINI_MODEL"
            expected = "gemini-embedding-001" if kind == "embedding" else "gemini-3.5-flash-lite"
            return AIConfigurationError(
                detail, **common, error_type="模型不可用（HTTP 404）",
                suggestion=f"请检查 {setting}；本版默认值为 {expected}。修改 .env 后重新运行",
            )
        if code in {400, 401, 403}:
            return AIConfigurationError(
                detail, **common, error_type=f"API 配置或权限错误（HTTP {code}）",
                suggestion="请检查 GEMINI_API_KEY、项目权限和模型可用列表；修正后重新运行",
            )
        if code == 429 or any(token in lower for token in ("resource_exhausted", "quota", "rate limit", "too many requests")):
            return AIRetryableError(
                detail, **common, error_type="限流或免费额度耗尽（HTTP 429）",
                suggestion="请稍后重试；新闻已保留为 pending/retry，不需要重新抓取",
            )
        if code in {408, 504} or any(token in lower for token in ("timeout", "timed out", "deadline")):
            return AIRetryableError(
                detail, **common, error_type="请求超时",
                suggestion="请检查网络后稍后重试；新闻已保留为 pending/retry",
            )
        if code is not None and 500 <= code <= 599:
            return AIRetryableError(
                detail, **common, error_type=f"Gemini 服务临时错误（HTTP {code}）",
                suggestion="服务端暂时不可用，请稍后重试；新闻数据不会丢失",
            )
        if self._is_retryable(exc):
            return AIRetryableError(
                detail, **common, error_type="临时 API 或网络错误",
                suggestion="请稍后重试；新闻已保留为 pending/retry",
            )
        if isinstance(exc, AIResponseError):
            return AIResponseError(
                detail, **common, error_type="AI 响应格式错误",
                suggestion="请保留数据库并重试；若持续出现，请检查当前模型的 Structured Output 支持",
            )
        return AIResponseError(
            detail, **common, error_type=f"Gemini API 错误{f'（HTTP {code}）' if code else ''}",
            suggestion="请检查 API Key、模型名称和网络设置后重试",
        )

    def _run(self, kind: str, operation):
        interval = max(0.0, float(getattr(getattr(self, "settings", None), "ai_min_call_interval", 0)))
        wait = interval - (time.monotonic() - getattr(self, "_last_call_at", 0.0))
        if wait > 0:
            time.sleep(wait)
        self.usage.calls += 1
        setattr(self.usage, f"{kind}_calls", getattr(self.usage, f"{kind}_calls") + 1)
        try:
            result = operation()
            self._last_call_at = time.monotonic()
            return result
        except (AIConfigurationError, AIResponseError, AIRetryableError) as exc:
            self._last_call_at = time.monotonic()
            self.usage.failures += 1
            raise self._classified_error(kind, exc) from exc
        except Exception as exc:
            self._last_call_at = time.monotonic()
            self.usage.failures += 1
            raise self._classified_error(kind, exc) from exc

    def embeddings(
        self,
        texts: list[str],
        on_batch: Callable[[int, list[list[float]]], None] | None = None,
    ) -> list[list[float]]:
        if not self.available:
            raise AIRetryableError("未配置 GEMINI_API_KEY")
        if not texts:
            return []

        output: list[list[float]] = []
        # 免费档 embedding 限额是 100 条/分钟（按条计，不按请求计）。
        # 因此按 50 条一批，批与批之间留出一个配额窗口，避免第二批直接 429。
        batch = 50
        for start in range(0, len(texts), batch):
            if start:
                time.sleep(62)
            chunk = texts[start:start + batch]

            def call(chunk=chunk):
                response = self._client.models.embed_content(
                    model=self.settings.gemini_embedding_model,
                    contents=chunk,
                    config=self._types.EmbedContentConfig(
                        task_type="CLUSTERING",
                        output_dimensionality=self.settings.embedding_dimensions,
                    ),
                )
                values = [list(item.values or []) for item in (response.embeddings or [])]
                if len(values) != len(chunk) or any(not item for item in values):
                    raise AIResponseError("Gemini embedding 返回数量或内容异常")
                return values

            # 撞上 429/限流时等一个配额窗口重试，最多补两次；仍失败则保留已算好的批次，
            # 让剩下的文章继续留在 pending，下一轮再处理，避免整批结果被丢弃导致死循环。
            for attempt in range(3):
                try:
                    values = self._run("embedding", call)
                    if on_batch:
                        on_batch(start, values)
                    output.extend(values)
                    break
                except AIRetryableError:
                    if attempt == 2:
                        if output:
                            print(f"[ai] embedding 限流未恢复，本轮先处理已完成的 {len(output)} 条，其余保持 pending")
                            return output
                        raise
                    self.usage.retries += 1
                    time.sleep(62)
        return output

    def _retrying_run(self, kind: str, operation, delays: tuple[float, ...] = (5.0, 15.0)):
        for attempt in range(len(delays) + 1):
            try:
                return self._run(kind, operation)
            except AIRetryableError:
                if attempt >= len(delays):
                    raise
                self.usage.retries += 1
                time.sleep(delays[attempt])

    def _structured(self, kind: str, schema: dict, instructions: str, payload: dict, max_tokens: int = 1200) -> dict:
        if not self.available:
            raise AIRetryableError("未配置 GEMINI_API_KEY")

        def call():
            response = self._client.models.generate_content(
                model=self.model_name,
                contents=json.dumps(payload, ensure_ascii=False, default=str),
                config=self._types.GenerateContentConfig(
                    system_instruction=instructions,
                    response_mime_type="application/json",
                    response_json_schema=schema,
                    max_output_tokens=max_tokens,
                ),
            )
            try:
                value = json.loads(response.text or "")
            except (TypeError, json.JSONDecodeError) as exc:
                raise AIResponseError("Gemini 未返回有效 JSON") from exc
            if not isinstance(value, dict):
                raise AIResponseError("Gemini 结构化结果不是对象")
            return value

        return self._retrying_run(kind, call)

    def decide_event_match(self, article: dict, candidates: list[dict]) -> dict:
        return self._structured(
            "clustering", MATCH_SCHEMA,
            "判断新报道与候选事件是否描述同一个核心现实事件。主题、人物或国家相同并不足以归并；主体、动作、时间和核心结果必须一致。social_primary、official 与 news_media 可以属于同一事件，不得因来源类型不同阻止归并。都不相同则 match=false 且 event_id 为空字符串。",
            {"article": article, "candidates": candidates},
        )

    def assess_importance(self, evidence: dict) -> dict:
        return self._structured(
            "importance", IMPORTANCE_SCHEMA,
            "你是严谨的中文新闻值班总编辑。根据整个事件及多源证据写克制的中文标题与两句内摘要，再判断现实影响、突发性、范围和可靠度。来源等级只是辅助信号，不能代替事件级判断。social_primary 只能证明人物公开说了什么，不能单独证明政策已经生效；官方或政治性表述必须保留‘白宫表示/宣布’等 attribution。不得靠战争、地震、降息等关键词打分；背景稿和旧闻不得判为突发。breaking=true 必须非常克制。",
            evidence,
        )

    def assess_substantive_update(self, previous: dict, current: dict) -> dict:
        return self._structured(
            "update", UPDATE_SCHEMA,
            "判断同一新闻事件相对上次简报是否出现实质性新进展。仅新增转载、重复表述、评论或无新事实的跟进不算；官方确认、结果变化、关键数字、重要行动或影响范围变化通常算。update_label 用简短中文说明进展。",
            {"previous_edition_snapshot": previous, "current_event": current},
        )

    def edit_digest(self, edition: str, events: list[dict]) -> str:
        if not self.available:
            raise AIRetryableError("未配置 GEMINI_API_KEY")

        def call():
            response = self._client.models.generate_content(
                model=self.model_name,
                contents=json.dumps({"edition": edition, "events": events}, ensure_ascii=False, default=str),
                config=self._types.GenerateContentConfig(
                    system_instruction=(
                        "你是严谨的中文新闻总编辑。只依据输入，不补充事实。输入已按重要性×市场影响×用户相关性排序，"
                        "并提供 digest_section。输出一份适合手机QQ阅读、接近 Reuters/Bloomberg Morning Brief 的 Markdown 简报。"
                        "第一行必须是 '# Daily News'，下一行写 edition 中的日期。严格按 '## 今日重点'（3至5条）、"
                        "'## 中国'、'## 全球政治'、'## 金融市场'、'## 科技'、'## 其他' 组织；没有内容的板块可省略。"
                        "每条新闻必须使用三级标题，格式只能是 '### ① [新闻标题](URL)' 或 '### [新闻标题](URL)'；"
                        "URL 必须原样使用 source_evidence 中与该事件标题和摘要相符的报道 canonical_url；只有无法对应时才使用 links。"
                        "禁止把另一篇报道的链接配给当前标题，禁止在标题下或正文中另行显示裸URL。"
                        "每条标题后写2至3句紧凑事实摘要，再单独写一行来源。来源行格式为 '来源：A｜B'，"
                        "其中 A、B 必须是该事件 source_evidence 里真实出现过的来源名（如 Reuters、BBC World、NHK），"
                        "按证据中出现的顺序写，最多两个；严禁写入输入中不存在的媒体名，"
                        "也不得照抄本提示里出现的任何示例媒体名。不写AI评论、投资建议、"
                        "大段背景或重复原标题。各板块之间使用 '---'。中国板块侧重宏观、政策、市场、外贸和大型企业，"
                        "金融市场侧重可能影响投资判断的央行、利率、通胀、就业、汇率、商品、市场波动与重要财报。"
                        "保持全球视角，不把普通社会新闻因发生在中国就抬到重点。同一个事件在整份简报里只能出现一次："
                        "‘今日重点’只是从各板块里挑出来的头部事件，凡是已经写进‘今日重点’的事件，绝不允许再出现在"
                        "‘中国’/‘全球政治’/‘金融市场’/‘科技’/‘其他’任何一个板块中；每个事件只归属一个板块。"
                        "带 is_update=true 的条目必须明确标成‘进展’或‘更新’。"
                        "对 White House 或 social_primary 的陈述保留‘白宫表示/特朗普在 X 表示’等 attribution，"
                        "不得把人物发言自动改写为政策已经生效。不显示后台分数、抓取数或未读数。"
                        "如果 edition 包含 breaking，则改用 '# Daily News Breaking'，并且不要输出任何板块标题"
                        "（不写‘今日重点’，也不写‘其他’）：整条消息只讲输入里那一个事件。"
                        "无论哪种版次，新闻标题仍必须是可点击的Markdown链接。"
                    ),
                    max_output_tokens=3200,
                ),
            )
            text = (response.text or "").strip()
            if not text:
                raise AIResponseError("Gemini 未返回简报正文")
            return text

        return self._retrying_run("editing", call)
