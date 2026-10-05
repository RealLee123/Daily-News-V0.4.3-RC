from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from pathlib import Path

import httpx

from daily_news.config import ROOT, Settings
from daily_news.delivery.qq import API, QQBot


PREVIEW_MARKDOWN = """# Daily News Markdown Preview

> 这是一条独立的 QQ Markdown 能力测试，不是正式日报。

## 今日重点

1. **[美联储释放新的利率信号](https://www.reuters.com/markets/)**

   美国最新经济数据令市场重新评估利率路径。

   来源：Reuters

## 中国与市场

- **人民币与中国资产走势受到关注**
- 黄金、原油和全球股市波动值得继续跟踪

---

如果标题可点击、粗体和层级正常显示，说明当前机器人账号已经获得原生 Markdown 消息能力。
"""


def build_markdown_body(content: str = PREVIEW_MARKDOWN) -> dict:
    return {
        "msg_type": 2,
        "markdown": {"content": content},
        "msg_seq": int(time.time() * 1000) % 1_000_000,
    }


def sqlite_path(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise RuntimeError("Markdown Preview 只读取本地 SQLite news.db，不支持其他 DATABASE_URL")
    raw = database_url[len(prefix):]
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def read_qq_target_read_only(path: Path) -> dict:
    if not path.exists():
        raise RuntimeError(f"找不到数据库：{path}")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        row = connection.execute("SELECT value FROM settings WHERE key='qq_target'").fetchone()
    finally:
        connection.close()
    if not row:
        raise RuntimeError("news.db 中没有 qq_target，请先运行现有 qq-smoke")
    target = json.loads(row[0])
    if target.get("type") not in {"c2c", "group"} or not target.get("openid"):
        raise RuntimeError("news.db 中的 qq_target 格式无效")
    return target


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def send_markdown_preview(settings: Settings, target: dict) -> dict:
    bot = QQBot(settings, database=None)
    result: dict = {}

    async def callback(_socket, payload):
        if payload is not None:
            return None
        path = f"/v2/{'groups' if target['type'] == 'group' else 'users'}/{target['openid']}/messages"
        token = await bot.token()
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{API}{path}",
                headers={"Authorization": f"QQBot {token}"},
                json=build_markdown_body(),
            )
        try:
            data = response.json()
        except ValueError:
            data = {"raw_response": response.text[:500]}
        if response.is_error or data.get("code"):
            code = data.get("code", response.status_code)
            if code == 40034127:
                advice = "当前机器人账号没有 Markdown 权限，请在 QQ 开放平台检查消息能力。"
            elif code == 1100102:
                advice = "当前机器人尚未获得该新功能体验资格。"
            else:
                advice = "请根据错误码检查机器人消息能力和内容格式。"
            raise RuntimeError(f"QQ Markdown Preview 发送失败：code={code}；{advice} 返回={data}")
        result.update(data)
        return "done"

    await bot.online(callback, timeout=30)
    return result


def main() -> None:
    settings = Settings()
    settings.require_qq()
    database_path = sqlite_path(settings.database_url)
    target = read_qq_target_read_only(database_path)
    before = file_sha256(database_path)

    print("[QQ Markdown Preview] 独立实验入口")
    print("正式 push：未调用；Gemini：0；Embedding：0；数据库：只读")
    print(f"目标类型：{target['type']}（OpenID 不输出）")
    result = asyncio.run(send_markdown_preview(settings, target))

    after = file_sha256(database_path)
    if before != after:
        raise RuntimeError("安全检查失败：Preview 前后 news.db 文件哈希发生变化")
    print("QQ Markdown 消息 API 返回成功。请在手机 QQ 检查标题、粗体和可点击链接效果。")
    print(f"消息 ID：{result.get('id', 'API 未返回')}；news.db 前后完全一致")


if __name__ == "__main__":
    main()
