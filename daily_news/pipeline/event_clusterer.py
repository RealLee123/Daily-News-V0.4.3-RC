from __future__ import annotations

from daily_news.ai import AIConfigurationError, AIProviderError, AIRetryableError
from daily_news.pipeline.breaking import is_breaking_candidate
from daily_news.pipeline.semantic import cosine_similarity, event_text, weighted_average


def _article_for_ai(article: dict) -> dict:
    return {
        "id": article["id"], "title": article["title"], "summary": article["summary"][:1800],
        "source": article["source_name"], "source_group": article["source_group"],
        "source_type": article.get("source_kind", "news_media"),
        "source_tier": article.get("source_tier", "standard"),
        "official": article["official"], "primary_source": article.get("primary_source", False),
        "default_weight": article.get("default_weight", 1.0), "region": article["region"],
        "category": article["category"], "published_at": article["published_at"],
    }


def _candidate_for_ai(event: dict, similarity: float) -> dict:
    return {
        "event_id": event["id"], "title": event["canonical_title"], "summary": event["summary"][:1200],
        "region": event["region"], "category": event["category"],
        "last_seen": event["last_seen"], "semantic_similarity": round(similarity, 4),
    }


def _importance_evidence(payload: dict, already_breaking: bool = False, previous_digest: dict | None = None) -> dict:
    articles = payload["articles"]
    previous_ids = set((previous_digest or {}).get("article_ids", []))
    return {
        "event": {
            "id": payload["id"], "title": payload["canonical_title"], "summary": payload["summary"],
            "first_seen": payload["first_seen"], "last_seen": payload["last_seen"],
            "article_count": payload["article_count"],
            "independent_source_count": payload["independent_source_count"],
            "official_source_count": payload["official_source_count"],
            "velocity_30m": payload["velocity_30m"],
            "already_sent_breaking_alert": already_breaking,
            "previous_assessment": {
                "importance": payload.get("importance"), "breaking": payload.get("breaking"),
                "scope": payload.get("scope"), "confidence": payload.get("confidence"),
                "reason": payload.get("importance_reason"), "assessed_at": payload.get("assessed_at"),
            },
            "change_since_last_digest": {
                "previously_in_digest": previous_digest is not None,
                "new_report_count": sum(item["id"] not in previous_ids for item in articles) if previous_digest else len(articles),
                "previous_snapshot": previous_digest,
            },
        },
        "reports": [{
            "source": item["source_name"], "source_group": item["source_group"],
            "source_kind": item["source_kind"], "official": item["official"],
            "source_tier": item.get("source_tier", "standard"),
            "primary_source": item.get("primary_source", False),
            "default_weight": item.get("default_weight", 1.0),
            "title": item["title"], "published_at": item["published_at"],
        } for item in articles[-20:]],
    }


def cluster_pending(settings, db, ai) -> dict:
    pending = db.pending_articles(limit=600)
    print(f"[cluster] 待处理文章 {len(pending)} 篇")
    changed: set[str] = set()
    llm_reviews = 0
    clustered = 0
    generated_embeddings = 0
    reused_embeddings = 0
    new_events = 0
    updated_events: set[str] = set()
    breaking_events = 0
    errors: list[str] = []

    # Same-source revisions inherit their article family's event mechanically. This works even
    # while Gemini is unavailable and never consumes embedding/LLM quota.
    semantic_pending = []
    for article in pending:
        inherited = db.event_for_family(article["source_id"], article["family_key"])
        if inherited:
            db.attach_article(inherited, article["id"], "family_version", 1.0)
            changed.add(inherited)
            updated_events.add(inherited)
            clustered += 1
        else:
            semantic_pending.append(article)

    print(f"[cluster] 同源版本直接归并 {clustered} 篇；余下 {len(semantic_pending)} 篇走语义聚类")
    events = db.recent_events(settings.event_lookback_hours)
    def valid_embedding(item: dict) -> bool:
        vector = item.get("embedding")
        expected_dimensions = getattr(settings, "embedding_dimensions", len(vector) if isinstance(vector, list) else 0)
        return (
            isinstance(vector, list) and bool(vector)
            and item.get("embedding_model") == ai.embedding_model_name
            and item.get("embedding_dimensions") == len(vector) == expected_dimensions
        )

    cached_ids = {item["id"] for item in semantic_pending if valid_embedding(item)}
    missing = [item for item in semantic_pending if not valid_embedding(item)]
    reused_embeddings = len(cached_ids)
    print(f"[embedding] 需要生成向量 {len(missing)} 篇；可复用已有向量 {reused_embeddings} 篇")
    if missing and ai.available:
        texts = [event_text(item["title"], item["summary"], item["region"], item["category"]) for item in missing]

        def checkpoint_batch(start: int, vectors: list[list[float]]) -> None:
            nonlocal generated_embeddings
            rows = [(missing[start + offset]["id"], vector) for offset, vector in enumerate(vectors)]
            db.save_article_embeddings(rows, ai.embedding_model_name)
            generated_embeddings += len(rows)

        try:
            ai.embeddings(texts, on_batch=checkpoint_batch)
        except AIProviderError as exc:
            message = f"embedding 暂缓：{exc}"
            errors.append(message)
            db.record_ai_failure("embedding", "pending_articles", str(exc), isinstance(exc, AIRetryableError))

    print(f"[embedding] 完成：新生成 {generated_embeddings}，复用 {reused_embeddings}")
    if missing and not ai.available:
        errors.append("AI 未配置；语义聚类文章保持 pending")

    # Reload the article checkpoints. Successful batches are now usable even when a later batch failed.
    processable = [item for item in db.pending_articles(limit=600) if valid_embedding(item)]
    for article in processable:
        embedding = article["embedding"]
        ranked = sorted(
            ((cosine_similarity(embedding, event["embedding"]), event) for event in events),
            key=lambda item: item[0], reverse=True,
        )[:settings.event_candidate_limit]
        best_score = ranked[0][0] if ranked else 0.0
        event_id = None
        method, confidence = "new_event", 1.0

        if ranked and best_score >= settings.event_auto_merge_threshold:
            event_id, method, confidence = ranked[0][1]["id"], "embedding_auto", best_score
        elif ranked and best_score >= settings.event_llm_review_threshold:
            if llm_reviews >= getattr(settings, "ai_max_cluster_reviews_per_run", 12):
                if "灰区裁决达到本轮上限，其余文章保留 pending" not in errors:
                    errors.append("灰区裁决达到本轮上限，其余文章保留 pending")
                continue
            llm_reviews += 1
            input_data = {
                "article": _article_for_ai(article),
                "candidates": [_candidate_for_ai(event, score) for score, event in ranked],
            }
            try:
                decision = ai.decide_event_match(input_data["article"], input_data["candidates"])
            except AIProviderError as exc:
                errors.append(f"事件 {article['id']} 灰区判断暂缓：{exc}")
                db.record_ai_failure("event_match", article["id"], str(exc), isinstance(exc, AIRetryableError))
                if isinstance(exc, (AIRetryableError, AIConfigurationError)):
                    break
                continue
            db.record_ai_decision("event_match", article["id"], input_data, decision, ai.model_name)
            allowed_ids = {event["id"] for _, event in ranked}
            if decision["match"] and decision["event_id"] in allowed_ids:
                event_id, method, confidence = decision["event_id"], "llm_review", float(decision["confidence"])

        if event_id is None:
            event_id = db.create_event(article, embedding)
            new_events += 1
            events.append({
                "id": event_id, "canonical_title": article["title"], "summary": article["summary"],
                "category": article["category"], "region": article["region"],
                "last_seen": article["published_at"], "embedding": embedding, "article_count": 0,
            })
        else:
            event = next(item for item in events if item["id"] == event_id)
            updated = weighted_average(event["embedding"], embedding, event.get("article_count", 1))
            db.update_event_embedding(event_id, updated)
            event["embedding"] = updated
            updated_events.add(event_id)

        db.attach_article(event_id, article["id"], method, confidence)
        changed.add(event_id)
        clustered += 1

    print(f"[cluster] 完成：新事件 {new_events}，更新已有事件 {len(updated_events)}")
    assessed = 0
    assessment_ids = list(dict.fromkeys([*changed, *db.pending_assessment_event_ids()]))
    assessment_ids = assessment_ids[:getattr(settings, "ai_max_assessments_per_run", 12)]
    for event_id in assessment_ids if ai.available else []:
        payload = db.event_payload(event_id)
        evidence = _importance_evidence(
            payload, db.was_event_breaking_delivered(event_id), db.latest_digest_snapshot(event_id)
        )
        try:
            assessment = ai.assess_importance(evidence)
        except AIProviderError as exc:
            errors.append(f"事件 {event_id} 重要性判断暂缓：{exc}")
            db.mark_assessment_retry(event_id, str(exc))
            db.record_ai_failure("importance", event_id, str(exc), isinstance(exc, AIRetryableError))
            if isinstance(exc, (AIRetryableError, AIConfigurationError)):
                break
            continue
        db.record_ai_decision("importance", event_id, evidence, assessment, ai.model_name)
        db.update_assessment(event_id, assessment)
        assessed += 1
        if is_breaking_candidate(db.event_payload(event_id), settings):
            breaking_events += 1

    print(f"[AI] 重要性判断 {assessed}；灰区判断 {llm_reviews}；暂缓/错误 {len(errors)} 条")
    remaining = len(db.pending_articles(limit=100000))

    return {
        "pending": len(pending), "remaining_pending": remaining,
        "clustered": clustered, "event_ids": sorted(changed), "llm_reviews": llm_reviews,
        "assessed": assessed, "pending_assessments": len(db.pending_assessment_event_ids()),
        "reused_embeddings": reused_embeddings, "generated_embeddings": generated_embeddings,
        "new_events": new_events, "updated_events": len(updated_events),
        "breaking_events": breaking_events, "errors": errors,
    }
