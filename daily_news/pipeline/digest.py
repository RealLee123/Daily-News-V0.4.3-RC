from __future__ import annotations

from datetime import datetime

from daily_news.ai import AIProviderError, AIRetryableError


SECTION_ORDER = {"今日重点": 0, "中国": 1, "全球政治": 2, "金融市场": 3, "科技": 4, "其他": 5}

CHINA_TERMS = (
    "中国", "china", "chinese", "beijing", "香港", "hong kong", "人民币", "renminbi", "yuan",
    "央行", "pboc", "a股", "港股", "沪深", "中美", "sino-", "出口", "房地产",
    "美中",
)
CHINA_PRIORITY_TERMS = (
    "宏观", "经济", "货币", "财政", "政策", "央行", "pboc", "利率", "降准", "降息", "汇率",
    "人民币", "yuan", "房地产", "产业", "科技", "半导体", "出口", "关税", "贸易", "a股", "港股",
    "上证", "恒生", "监管", "腾讯", "阿里", "byd", "比亚迪",
)
MARKET_TERMS = (
    "美联储", "fed", "federal reserve", "欧洲央行", "ecb", "日本央行", "boj", "中国央行", "pboc",
    "利率", "降息", "加息", "通胀", "cpi", "ppi", "就业数据", "非农", "失业率", "payroll", "汇率", "美元", "日元",
    "人民币", "债券", "国债", "收益率", "股市", "股票", "a股", "港股", "标普", "nasdaq", "纳斯达克",
    "oil", "原油", "黄金", "gold", "commodity", "大宗商品", "财报", "earnings", "业绩预告",
    "ipo", "上市", "并购", "acquisition", "金融监管",
)
POLITICS_TERMS = (
    "政府", "总统", "总理", "议会", "选举", "外交", "战争", "停火", "制裁", "关税", "白宫",
    "普京", "俄罗斯", "乌克兰", "伊朗", "以色列", "军事", "国防", "安全", "政府", "总统", "总理",
    "government", "president", "parliament", "election", "war", "ceasefire", "sanction", "tariff",
)
TECH_TERMS = (
    "科技", "人工智能", "ai", "半导体", "芯片", "互联网", "软件", "数据中心", "机器人",
    "technology", "semiconductor", "chip", "artificial intelligence", "robot",
)


def _event_text(event: dict) -> str:
    return " ".join(str(event.get(key, "")) for key in ("canonical_title", "summary", "category", "region")).lower()


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term.lower() in text for term in terms)


def digest_rank(event: dict) -> tuple[float, str]:
    """Return transient digest priority and section without changing event importance."""
    text = _event_text(event)
    category = str(event.get("category", "")).lower()
    region = str(event.get("region", "")).lower()
    importance = max(0.0, min(100.0, float(event.get("importance") or 0))) / 100.0

    china_related = _contains_any(text, CHINA_TERMS) or region in {"china", "中国", "hong kong", "香港"}
    china_priority = china_related and (
        _contains_any(text, CHINA_PRIORITY_TERMS)
        or category in {"economy", "business", "finance", "markets", "technology", "politics", "trade"}
    )
    market_related = _contains_any(text, MARKET_TERMS) or category in {"finance", "markets", "commodities"}
    politics_related = _contains_any(text, POLITICS_TERMS) or category in {"politics", "world"}
    tech_related = _contains_any(text, TECH_TERMS) or category in {"technology", "tech", "ai"}

    market_impact = 1.25 if market_related else 1.0
    user_relevance = 1.0
    if china_priority:
        user_relevance *= 1.24
    elif china_related:
        user_relevance *= 1.04
    if market_related:
        user_relevance *= 1.18
    if category in {"society", "culture", "sports", "lifestyle"} and not market_related:
        user_relevance *= 0.90

    evidence_bonus = min(int(event.get("independent_source_count") or 0), 3) * 0.01
    score = importance * market_impact * user_relevance + evidence_bonus

    if china_related:
        section = "中国"
    elif market_related:
        section = "金融市场"
    elif tech_related:
        section = "科技"
    elif politics_related:
        section = "全球政治"
    else:
        section = "其他"
    return score, section


def prioritize_digest_events(events: list[dict]) -> list[dict]:
    ranked = []
    for event in events:
        score, section = digest_rank(event)
        item = dict(event)
        item["digest_priority"] = round(score, 4)
        item["digest_section"] = section
        ranked.append(item)

    def sort_key(item: dict):
        last_seen = item.get("last_seen")
        timestamp = last_seen.timestamp() if isinstance(last_seen, datetime) else 0.0
        return (item["digest_priority"], float(item.get("importance") or 0), timestamp)

    return sorted(ranked, key=sort_key, reverse=True)


def event_for_editor(event: dict) -> dict:
    articles = event.get("articles", [])
    return {
        "id": event["id"], "title": event["canonical_title"], "summary": event["summary"],
        "category": event["category"], "region": event["region"],
        "first_seen": event["first_seen"], "last_seen": event["last_seen"],
        "importance": event["importance"], "scope": event.get("scope"),
        "confidence": event.get("confidence"), "reason": event.get("importance_reason"),
        "independent_source_count": event["independent_source_count"],
        "official_source_count": event["official_source_count"],
        "sources": sorted({item["source_name"] for item in articles}),
        "source_evidence": [{
            "source": item["source_name"], "source_group": item["source_group"],
            "source_type": item.get("source_kind", "news_media"), "official": item.get("official", False),
            "primary_source": item.get("primary_source", False), "title": item["title"],
            "canonical_url": item.get("canonical_url", ""),
        } for item in articles[-20:]],
        "links": [item["canonical_url"] for item in articles[-3:] if item.get("canonical_url")],
        "is_update": bool(event.get("is_update")),
        "update_label": event.get("update_label", ""),
        "digest_section": event.get("digest_section", "其他"),
        "digest_priority": event.get("digest_priority"),
    }


def prepare_digest_events(settings, db, ai, start, end) -> tuple[list[dict], dict]:
    candidates = prioritize_digest_events(db.events_between(start, end, settings.max_digest_events * 2))
    selected: list[dict] = []
    stats = {"candidates": len(candidates), "new": 0, "updates": 0, "repeated_skipped": 0, "update_failures": 0}

    for event in candidates:
        previous = db.latest_digest_snapshot(event["id"])
        if previous is None:
            event["is_update"] = False
            selected.append(event)
            stats["new"] += 1
        else:
            current = db.digest_snapshot(event)
            previous_ids = set(previous.get("article_ids", []))
            if set(current.get("article_ids", [])) <= previous_ids:
                stats["repeated_skipped"] += 1
                continue
            try:
                decision = ai.assess_substantive_update(previous, current)
            except AIProviderError as exc:
                db.record_ai_failure("substantive_update", event["id"], str(exc))
                stats["update_failures"] += 1
                if isinstance(exc, AIRetryableError):
                    break
                continue
            db.record_ai_decision("substantive_update", event["id"],
                                  {"previous": previous, "current": current}, decision, ai.model_name)
            if not decision["substantive"]:
                stats["repeated_skipped"] += 1
                continue
            event["is_update"] = True
            event["update_label"] = decision["update_label"]
            selected.append(event)
            stats["updates"] += 1
        if len(selected) >= settings.max_digest_events:
            break
    return prioritize_digest_events(selected), stats
