from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from time import perf_counter
from zoneinfo import ZoneInfo

from daily_news.ai import AIProviderError, create_ai_provider
from daily_news.audit import database_snapshot, format_database_snapshot
from daily_news.collectors import collect_all
from daily_news.delivery import (
    QQBot, build_event_markdown_digest, qq_messages_from_markdown, split_qq_markdown,
)
from daily_news.pipeline import (
    cluster_pending, event_for_editor, is_breaking_candidate, prepare_digest_events,
    prioritize_digest_events,
)


def _as_db_time(moment: datetime) -> datetime:
    """Normalize a window boundary to UTC before it is bound into a query.

    Stored timestamps are UTC strings (``2026-10-04 10:10:44+00:00``) and SQLite
    compares them as text. Binding an aware ``Asia/Shanghai`` value would embed a
    ``+08:00`` suffix, and that string sorts *eight hours later* than every stored
    value -- so a ``now - 3h`` cutoff built from local time silently matches nothing.
    Convert at the boundary; keep local time only for display and edition labels.
    """
    return moment.astimezone(timezone.utc)


async def collect(settings, db, ai=None) -> dict:
    started_at = datetime.now(ZoneInfo(settings.timezone))
    timer = perf_counter()
    ai = ai or create_ai_provider(settings)
    articles, errors = await collect_all(settings.sources_file, settings.fetch_timeout, settings)
    for error in errors:
        print(f"[source] {error}")
    ingest = db.ingest_articles(articles)
    cluster = cluster_pending(settings, db, ai)
    for error in cluster.get("errors", []):
        print(f"[ai] {error}")
    elapsed = perf_counter() - timer
    usage = ai.usage.to_dict()
    pending = db.pending_counts()
    stats = {
        "started_at": started_at, "rss_items": len(articles), "existing_articles": ingest.exact_duplicates,
        "new_articles": len(ingest.inserted_ids), "reused_embeddings": cluster["reused_embeddings"],
        "generated_embeddings": cluster["generated_embeddings"], "new_events": cluster["new_events"],
        "updated_events": cluster["updated_events"], "llm_reviews": cluster["llm_reviews"],
        "assessed": cluster["assessed"], "breaking_events": cluster["breaking_events"],
        "pending_articles": pending["article_pipeline"],
        "pending_assessments": pending["event_assessment"], "pending_total": pending["total"],
        "ai_calls": usage["calls"], "ai_failures": usage["failures"],
        "ai_retries": usage["retries"], "elapsed_seconds": elapsed,
    }
    print("\n=== V0.4.3 增量运行摘要 ===")
    print(f"本轮开始：{started_at.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"来源获取文章：{stats['rss_items']}")
    print(f"数据库已有/精确重复：{stats['existing_articles']}")
    print(f"新增文章：{stats['new_articles']}（同源更新 {ingest.new_versions}）")
    print(f"复用已有 Embedding：{stats['reused_embeddings']}")
    print(f"新生成 Embedding：{stats['generated_embeddings']}")
    print(f"新事件：{stats['new_events']}；更新已有事件：{stats['updated_events']}")
    print(f"Gemini 灰区判断：{stats['llm_reviews']}；重要性判断：{stats['assessed']}")
    print(f"重大事件：{stats['breaking_events']}")
    print(f"待聚类文章：{stats['pending_articles']}；待重要性评估事件：{stats['pending_assessments']}；待处理总计：{stats['pending_total']}")
    print(f"AI 调用：{stats['ai_calls']}；失败：{stats['ai_failures']}；网络重试：{stats['ai_retries']}")
    print(f"总耗时：{stats['elapsed_seconds']:.1f} 秒")
    if cluster.get("remaining_pending"):
        print(f"仍有 {cluster['remaining_pending']} 篇文章保持 pending，下次运行会继续处理。")
    return {"articles": len(articles), "source_errors": errors, "ingest": ingest, "cluster": cluster, "ai": ai, "stats": stats}


def _edition_window(settings, kind: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    zone = ZoneInfo(settings.timezone)
    now = now.astimezone(zone) if now else datetime.now(zone)
    if kind == "morning":
        start = datetime.combine(now.date() - timedelta(days=1), time.fromisoformat(settings.evening_time), zone)
        end = datetime.combine(now.date(), time.fromisoformat(settings.morning_time), zone)
        return start, end
    start = datetime.combine(now.date(), time.fromisoformat(settings.morning_time), zone)
    end = datetime.combine(now.date(), time.fromisoformat(settings.evening_time), zone)
    return start, end


async def digest_job(settings, db, kind: str) -> None:
    """Render and push one edition from Events that are already in the database.

    Deliberately does not collect. Ingestion, deduplication, embedding, clustering and
    importance assessment all belong to the two-hourly scan (``news-scan``): by the
    time an edition runs, its window has already been processed. The digest only
    selects the Events in the window and has the AI edit them.
    """
    settings.require_ai()
    ai = create_ai_provider(settings)
    start, end = _edition_window(settings, kind)

    # Idempotency first. Once this edition has been delivered, running it again must be a
    # quiet no-op: if the check sat after event selection, a repeat would find every event
    # already digested, end up with zero candidates and die on the "no new events" error --
    # safe, but a traceback and a non-zero exit code in the scheduler's log every run.
    delivery_id = f"{kind}:{start.date().isoformat()}"
    if db.was_delivered(delivery_id):
        print("本期已经推送，跳过重复发送")
        return

    events, selection = prepare_digest_events(settings, db, ai, _as_db_time(start), _as_db_time(end))
    if not events:
        raise RuntimeError(f"本期没有新的或出现实质性进展的事件（已过滤重复 {selection['repeated_skipped']} 条）")
    edition_name = "早报" if kind == "morning" else "晚报"
    text = ai.edit_digest(f"{edition_name} {end.date().isoformat()}", [event_for_editor(event) for event in events])
    bot = QQBot(settings, db)
    try:
        messages = split_qq_markdown(text, settings.qq_message_max_chars)
    except ValueError as exc:
        print(f"[digest] Gemini 返回的 Markdown 不符合手机简报规则，使用 Event 安全模板：{exc}")
        text = build_event_markdown_digest(events, end.date())
        messages = split_qq_markdown(text, settings.qq_message_max_chars)
    await bot.push_many_markdown_while_online(messages)
    db.mark_delivered(delivery_id, kind, db.get_setting("qq_target"))
    db.record_digest_events(delivery_id, kind, events)
    print(f"{kind} 推送完成：{len(messages)} 段；新事件 {selection['new']}，实质性进展 {selection['updates']}")


def pending_breaking_alerts(settings, db) -> list[dict]:
    """Breaking events that are ready to alert right now, highest priority first.

    Two things a two-hourly scan needs and the original single-shot version did not
    have: a lookback window that comfortably exceeds the scan interval, and more than
    one alert per run. With a three-hour window a two-hour cadence gave an event
    roughly one chance -- if a higher-priority story took that run's only slot, the
    event left the pool before the next run and was never alerted at all.

    ``db.breaking_event_pool`` already orders by importance then ``last_seen``, so the
    cap keeps the most important ones. Every returned event has passed the one final
    reliability gate (``is_breaking_candidate``) and has not been pushed before.
    """
    # "N hours back from now" is the same instant in any zone, so no conversion is
    # needed here -- unlike the edition windows, which are local wall-clock times.
    start = datetime.now(timezone.utc) - timedelta(hours=settings.breaking_pool_hours)
    ready: list[dict] = []
    for event in db.breaking_event_pool(start):
        if not is_breaking_candidate(event, settings):
            continue
        if db.was_delivered(f"breaking:{event['id']}"):
            continue
        ready.append(event)
        if len(ready) >= settings.breaking_max_alerts_per_run:
            break
    return ready


async def breaking_job(settings, db) -> None:
    started = perf_counter()
    print("[news-scan] 开始扫描：抓取 → Embedding → 聚类 → AI 评估 → breaking 判断")
    settings.require_ai()
    ai = create_ai_provider(settings)
    await collect(settings, db, ai)
    alerts = pending_breaking_alerts(settings, db)
    for event in alerts:
        text = ai.edit_digest("breaking", [event_for_editor(event)])
        bot = QQBot(settings, db)
        # Use the same official Markdown path as the digests: qq_messages_from_markdown()
        # converts back to plain text and left the raw [title](url) syntax inside the
        # heading. QQ's native Markdown (msg_type=2) renders the title as a real link.
        try:
            messages = split_qq_markdown(text, settings.qq_message_max_chars)
        except ValueError as exc:
            print(f"[breaking] Gemini 返回的 Markdown 不符合手机简报规则，使用 Event 安全模板：{exc}")
            text = build_event_markdown_digest([event], datetime.now(timezone.utc).date())
            messages = split_qq_markdown(text, settings.qq_message_max_chars)
        await bot.push_many_markdown_while_online(messages)
        db.mark_delivered(f"breaking:{event['id']}", "breaking", db.get_setting("qq_target"), event["id"])
        print(f"重大新闻已推送：{event['canonical_title']}")
    if not alerts:
        print("本轮没有通过 AI 判断与可靠性门槛（多源或官方确认）的重大事件")
        print(f"[news-scan] 扫描完成，耗时 {perf_counter() - started:.1f} 秒")
        return
    print(f"本轮推送 {len(alerts)} 条重大新闻提醒")
    print(f"[news-scan] 扫描完成，耗时 {perf_counter() - started:.1f} 秒")


def _report_markdown(settings, now: datetime, run: dict, events: list[dict], digest: str, edit_error: str | None) -> str:
    ingest, cluster, ai = run["ingest"], run["cluster"], run["ai"]
    important = [event for event in events if (event.get("importance") or 0) >= 70]
    breaking = [event for event in events if is_breaking_candidate(event, settings)]
    usage, stats = ai.usage.to_dict(), run["stats"]
    lines = [
        "# Daily News｜本地新闻 AI 测试报告", "",
        f"生成时间：{now.isoformat(timespec='seconds')}",
        "测试范围：最近 24 小时；本次测试不会调用 QQ。", "", "## 流水线统计", "",
        "| 指标 | 数量 |", "|---|---:|",
        f"| 抓取文章数 | {run['articles']} |",
        f"| 精确去重后新增文章数 | {len(ingest.inserted_ids)} |",
        f"| 精确重复文章数 | {ingest.exact_duplicates} |",
        f"| 同源更新版本数 | {ingest.new_versions} |",
        f"| 复用已有 Embedding | {stats['reused_embeddings']} |",
        f"| 新生成 Embedding | {stats['generated_embeddings']} |",
        f"| 本轮完成聚类文章数 | {cluster['clustered']} |",
        f"| 新事件 / 更新已有事件 | {stats['new_events']} / {stats['updated_events']} |",
        f"| AI 已判断重点事件数（≥70） | {len(important)} |",
        f"| 等待后续 AI 评估的事件数 | {cluster.get('pending_assessments', 0)} |",
        f"| breaking 候选数（已过多源/官方门槛） | {len(breaking)} |",
        f"| AI 调用次数 | {usage['calls']} |",
        f"| AI 失败次数 | {usage['failures']} |",
        f"| AI 网络重试次数 | {usage['retries']} |",
        f"| 灰区聚类裁决次数 | {cluster['llm_reviews']} |", "",
        f"待聚类文章：{stats['pending_articles']}；待重要性评估事件：{stats['pending_assessments']}；"
        f"待处理总计：{stats['pending_total']}；本轮耗时：{stats['elapsed_seconds']:.1f} 秒。", "",
    ]
    if run["source_errors"] or cluster.get("errors") or edit_error:
        lines.extend(["## 可重试问题", ""])
        lines.extend(f"- 新闻源：{item}" for item in run["source_errors"])
        lines.extend(f"- AI：{item}" for item in cluster.get("errors", []))
        if edit_error:
            lines.append(f"- 简报编辑：{edit_error}")
        lines.append("")
    lines.extend(["## 测试版中文简报", "", digest or "本轮未能生成简报；新闻与待处理状态已保存在数据库，下次运行会继续。", ""])
    return "\n".join(lines)


async def news_ai_test(settings, db) -> Path:
    settings.require_ai()
    ai = create_ai_provider(settings)
    print(f"AI Provider：{settings.ai_provider}")
    print(f"文本模型：{settings.gemini_model}")
    print(f"Embedding 模型：{settings.gemini_embedding_model}")
    print("本地测试模式：不会发送 QQ 消息。\n")
    run = await collect(settings, db, ai)
    now = datetime.now(ZoneInfo(settings.timezone))
    recent = db.events_between(_as_db_time(now - timedelta(hours=24)),
                               _as_db_time(now + timedelta(minutes=1)), settings.max_digest_events)
    digest, edit_error = "", None
    if recent:
        try:
            digest = ai.edit_digest("本地测试版（最近24小时）", [event_for_editor(event) for event in recent])
        except AIProviderError as exc:
            edit_error = str(exc)
            db.record_ai_failure("digest_edit", "local-test", edit_error)
    else:
        edit_error = "最近 24 小时没有已完成 AI 评估的事件"
    report = _report_markdown(settings, now, run, recent, digest, edit_error)
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    path = settings.output_dir / f"新闻AI测试报告-{now.strftime('%Y%m%d-%H%M%S')}.md"
    path.write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"\n报告已保存：{path.resolve()}")
    return path


def _preview_event_markdown(event: dict, number: int) -> str:
    articles = event.get("articles", [])
    sources = sorted({item.get("source_name", "") for item in articles if item.get("source_name")})
    links = [item.get("canonical_url") for item in reversed(articles) if item.get("canonical_url")]
    title = event.get("canonical_title") or "未命名事件"
    summary = (event.get("summary") or "").strip()
    lines = [f"{number}. **{title}**"]
    if summary:
        lines.extend(["", summary])
    if sources:
        lines.extend(["", f"来源：{'｜'.join(sources)}"])
    if links:
        lines.extend(["", "链接：", links[0]])
    return "\n".join(lines)


def build_qq_preview_digest(events: list[dict]) -> str:
    ranked = prioritize_digest_events(events)
    top_count = min(5, len(ranked))
    top, remaining = ranked[:top_count], ranked[top_count:]
    lines = [
        "# Daily News V0.4.3 Preview Test", "",
        "> 仅测试 QQ 纯文本显示与分段；未调用 Gemini，未写入正式投递历史。", "",
        "## 今日重点", "",
    ]
    for index, event in enumerate(top, 1):
        lines.extend([_preview_event_markdown(event, index), ""])

    for section in ("中国", "全球政治", "金融市场", "科技", "其他"):
        section_events = [event for event in remaining if event.get("digest_section") == section]
        if not section_events:
            continue
        lines.extend([f"## {section}", ""])
        for index, event in enumerate(section_events, 1):
            lines.extend([_preview_event_markdown(event, index), ""])
    return "\n".join(lines).strip()


async def qq_preview_test(settings, db) -> dict:
    """Read assessed Events and test QQ rendering without AI or database writes."""
    zone = ZoneInfo(settings.timezone)
    now = datetime.now(zone)
    start = now - timedelta(hours=settings.qq_preview_hours)
    events = db.events_between(_as_db_time(start), _as_db_time(now + timedelta(minutes=1)),
                               max(settings.max_digest_events * 3, 60))
    events = prioritize_digest_events(events)[:settings.max_digest_events]
    if not events:
        raise RuntimeError(f"当前数据库最近 {settings.qq_preview_hours} 小时没有可用于 Preview 的已评估 Event")

    before = database_snapshot(db)
    markdown = build_qq_preview_digest(events)
    messages = qq_messages_from_markdown(markdown, settings.qq_message_max_chars)
    if not messages or not messages[0].startswith("【Daily News V0.4.3 Preview Test】"):
        raise RuntimeError("QQ Preview Formatter 未生成预期标题")

    print("QQ Preview：只读取当前数据库，不调用 Gemini/Embedding，不写 delivery/digest 历史。")
    print(f"Preview Event：{len(events)}；QQ 分段：{len(messages)}")
    print("每段字符数：" + ", ".join(str(len(message)) for message in messages))
    bot = QQBot(settings, db)
    try:
        await bot.push_many_while_online(messages)
    finally:
        after = database_snapshot(db)
        if after != before:
            raise RuntimeError(f"QQ Preview 检测到数据库变化：before={before}, after={after}")
    print("QQ Preview 推送成功；数据库前后完全一致。Gemini 调用：0；新 Embedding：0。")
    return {"events": len(events), "messages": len(messages), "before": before, "after": after}


async def qq_markdown_preview_test(settings, db) -> dict:
    """Render existing Events as a real Morning-Brief-style QQ Markdown digest, read-only."""
    zone = ZoneInfo(settings.timezone)
    now = datetime.now(zone)
    start = now - timedelta(hours=settings.qq_preview_hours)
    events = db.events_between(_as_db_time(start), _as_db_time(now + timedelta(minutes=1)),
                               max(settings.max_digest_events * 3, 60))
    events = prioritize_digest_events(events)[:settings.max_digest_events]
    if not events:
        raise RuntimeError(f"当前数据库最近 {settings.qq_preview_hours} 小时没有可用于 Markdown Preview 的 Event")

    before = database_snapshot(db)
    markdown = build_event_markdown_digest(
        events,
        now.date(),
        preview_label="【Daily News V0.4.3 Markdown Preview】",
    )
    messages = split_qq_markdown(markdown, settings.qq_message_max_chars)
    if not messages or "【Daily News V0.4.3 Markdown Preview】" not in messages[0]:
        raise RuntimeError("QQ Markdown Preview 未生成预期标记")

    print("QQ Markdown Preview：只读取当前数据库，不调用 Gemini/Embedding，不写正式历史。")
    print(f"Preview Event：{len(events)}；Markdown 分段：{len(messages)}")
    print("每段字符数：" + ", ".join(str(len(message)) for message in messages))
    bot = QQBot(settings, db)
    try:
        await bot.push_many_markdown_while_online(messages)
    finally:
        after = database_snapshot(db)
        if after != before:
            raise RuntimeError(f"QQ Markdown Preview 检测到数据库变化：before={before}, after={after}")
    print("QQ Markdown Preview 推送成功；数据库前后完全一致。Gemini 调用：0；新 Embedding：0。")
    return {"events": len(events), "messages": len(messages), "before": before, "after": after}


async def run_news_job(settings, db) -> None:
    now = datetime.now(ZoneInfo(settings.timezone))
    kind = "morning" if now.time() < time.fromisoformat("15:30") else "evening"
    print(f"run-news 自动选择：{'早报' if kind == 'morning' else '晚报'}（{settings.timezone} {now:%H:%M}）")
    await digest_job(settings, db, kind)


def print_database_audit(db) -> dict[str, int]:
    snapshot = database_snapshot(db)
    print("=== Daily News 数据库只读审计 ===")
    print(format_database_snapshot(snapshot))
    # An embedding always belongs to some article row -- including versions that were
    # superseded later and kept their vector -- so the ceiling is the total row count,
    # not the number of current versions.
    if snapshot["embedded_articles"] > snapshot["articles"]:
        raise RuntimeError("Embedding 数量异常：超过文章总行数")
    if snapshot["event_links"] > snapshot["articles"]:
        raise RuntimeError("Event 关联数量异常：超过文章总数")
    print("数据检查通过；未修改数据库。")
    return snapshot
