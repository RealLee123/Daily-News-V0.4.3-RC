from __future__ import annotations

from datetime import datetime, timezone

import httpx

from daily_news.models import Article
from daily_news.pipeline.article_identity import canonicalize_url, make_content_hash, make_family_key, normalize_text
from daily_news.source_registry import SourceDefinition


async def collect_x_user_timeline(
    client: httpx.AsyncClient, source: SourceDefinition, bearer_token: str
) -> list[Article]:
    if not bearer_token:
        raise RuntimeError("缺少 X_BEARER_TOKEN；Trump X source 已跳过")
    headers = {"Authorization": f"Bearer {bearer_token}"}
    username = (source.options or {}).get("username", "realDonaldTrump")
    user_response = await client.get(f"https://api.x.com/2/users/by/username/{username}", headers=headers)
    user_response.raise_for_status()
    user_id = user_response.json()["data"]["id"]
    timeline = await client.get(
        f"https://api.x.com/2/users/{user_id}/tweets", headers=headers,
        params={"max_results": 20, "exclude": "retweets,replies", "tweet.fields": "created_at"},
    )
    timeline.raise_for_status()
    result: list[Article] = []
    for post in timeline.json().get("data", []):
        post_id, post_text = str(post.get("id", "")), normalize_text(post.get("text", ""))
        if not post_id or not post_text:
            continue
        url = f"https://x.com/{username}/status/{post_id}"
        title = f"Donald Trump 在 X 表示：{post_text[:180]}"
        summary = (
            f"Donald Trump 在其官方 X 账号 @{username} 公开发布上述表述；"
            "这只能证明其公开说法，不代表相关政策已经正式生效。"
        )
        canonical = canonicalize_url(url)
        result.append(Article(
            **source.article_metadata(), title=title, url=url, canonical_url=canonical,
            external_id=post_id, content_hash=make_content_hash(title, summary),
            family_key=make_family_key(source.source_id, post_id, canonical, title),
            published_at=datetime.fromisoformat(post["created_at"].replace("Z", "+00:00"))
            if post.get("created_at") else datetime.now(timezone.utc), summary=summary,
        ))
    return result
