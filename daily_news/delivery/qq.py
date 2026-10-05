from __future__ import annotations

import asyncio
import json
import time

import httpx
import websockets

API = "https://api.bot.qq.com"
INTENTS_GROUP_AND_C2C = 1 << 25


class QQBot:
    def __init__(self, settings, database):
        self.settings = settings
        self.database = database
        self.access_token = ""
        self.expires_at = 0.0

    async def token(self) -> str:
        if self.access_token and time.time() < self.expires_at - 60:
            return self.access_token
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{API}/app/getAppAccessToken", json={
                "appId": self.settings.qq_app_id,
                "clientSecret": self.settings.qq_app_secret,
            })
            data = response.json()
        if "access_token" not in data:
            raise RuntimeError(f"获取 QQ AccessToken 失败：{data}")
        self.access_token = data["access_token"]
        self.expires_at = time.time() + int(data.get("expires_in", 7200))
        return self.access_token

    async def _gateway(self) -> str:
        token = await self.token()
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(f"{API}/gateway/bot", headers={"Authorization": f"QQBot {token}"})
            response.raise_for_status()
            return response.json()["url"]

    async def _identify(self, socket) -> None:
        await socket.send(json.dumps({
            "op": 2,
            "d": {
                "token": f"QQBot {await self.token()}",
                "intents": INTENTS_GROUP_AND_C2C,
                "shard": [0, 1],
                "properties": {"$os": "python", "$browser": "daily-news", "$device": "daily-news"},
            },
        }))

    async def _heartbeat(self, socket, seconds: float, sequence: dict) -> None:
        while True:
            await asyncio.sleep(seconds)
            await socket.send(json.dumps({"op": 1, "d": sequence["value"]}))

    async def online(self, callback, timeout: float = 60) -> None:
        url = await self._gateway()
        async with websockets.connect(url, open_timeout=20, close_timeout=5) as socket:
            sequence = {"value": None}
            heartbeat_task = None
            try:
                async with asyncio.timeout(timeout):
                    async for raw in socket:
                        payload = json.loads(raw)
                        if payload.get("s") is not None:
                            sequence["value"] = payload["s"]
                        if payload["op"] == 10:
                            heartbeat_task = asyncio.create_task(self._heartbeat(socket, payload["d"]["heartbeat_interval"] / 1000, sequence))
                            await self._identify(socket)
                        elif payload["op"] == 0 and payload.get("t") == "READY":
                            result = await callback(socket, None)
                            if result == "done":
                                return
                        elif payload["op"] == 0:
                            result = await callback(socket, payload)
                            if result == "done":
                                return
            finally:
                if heartbeat_task:
                    heartbeat_task.cancel()

    @staticmethod
    def target_from_event(payload: dict) -> dict | None:
        event, data = payload.get("t"), payload.get("d", {})
        if event in {"C2C_MESSAGE_CREATE", "FRIEND_ADD", "C2C_MSG_RECEIVE"}:
            openid = data.get("author", {}).get("user_openid") or data.get("author", {}).get("id") or data.get("openid")
            return {"type": "c2c", "openid": openid, "msg_id": data.get("id")} if openid else None
        if event in {"GROUP_AT_MESSAGE_CREATE", "GROUP_ADD_ROBOT", "GROUP_MSG_RECEIVE"}:
            openid = data.get("group_openid")
            return {"type": "group", "openid": openid, "msg_id": data.get("id")} if openid else None
        return None

    async def smoke_test(self) -> None:
        print("QQ Bot 已上线。现在向机器人发送任意消息；这次联调会回复一次“连接成功”。")

        async def callback(_socket, payload):
            if not payload:
                return None
            target = self.target_from_event(payload)
            if not target or target["type"] != self.settings.qq_target_type or not target["msg_id"]:
                return None
            self.database.set_setting("qq_target", {"type": target["type"], "openid": target["openid"]})
            await self.push("连接成功", target={"type": target["type"], "openid": target["openid"]}, msg_id=target["msg_id"])
            print("收发测试成功，推送目标已写入数据库。生产任务不会回复消息。")
            return "done"

        await self.online(callback, timeout=180)

    async def push(self, content: str, target: dict | None = None, msg_id: str | None = None) -> dict:
        target = target or self.database.get_setting("qq_target")
        if not target:
            raise RuntimeError("数据库中没有 qq_target，请先执行 qq-smoke")
        path = f"/v2/{'groups' if target['type'] == 'group' else 'users'}/{target['openid']}/messages"
        body = {"content": content, "msg_type": 0, "msg_seq": int(time.time() * 1000) % 1_000_000}
        if msg_id:
            body["msg_id"] = msg_id
        token = await self.token()
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{API}{path}", headers={"Authorization": f"QQBot {token}"}, json=body)
            data = response.json()
        if response.is_error or data.get("code"):
            raise RuntimeError(f"QQ 推送失败：{data}")
        return data

    async def push_markdown(self, markdown: str, target: dict | None = None, msg_id: str | None = None) -> dict:
        """Send official QQ native Markdown without changing the existing plain-text push path."""
        target = target or self.database.get_setting("qq_target")
        if not target:
            raise RuntimeError("数据库中没有 qq_target，请先执行 qq-smoke")
        path = f"/v2/{'groups' if target['type'] == 'group' else 'users'}/{target['openid']}/messages"
        body = {
            "msg_type": 2,
            "markdown": {"content": markdown},
            "msg_seq": int(time.time() * 1000) % 1_000_000,
        }
        if msg_id:
            body["msg_id"] = msg_id
        token = await self.token()
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{API}{path}", headers={"Authorization": f"QQBot {token}"}, json=body,
            )
            data = response.json()
        if response.is_error or data.get("code"):
            raise RuntimeError(f"QQ Markdown 推送失败：{data}")
        return data

    async def push_while_online(self, content: str) -> dict:
        results = await self.push_many_while_online([content])
        return results[0]

    async def push_many_while_online(self, contents: list[str]) -> list[dict]:
        if not contents:
            return []
        results: list[dict] = []

        async def callback(_socket, payload):
            if payload is None:
                for index, content in enumerate(contents):
                    results.append(await self.push(content))
                    if index + 1 < len(contents):
                        await asyncio.sleep(0.05)
                return "done"
            return None

        await self.online(callback, timeout=30)
        return results

    async def push_many_markdown_while_online(self, markdown_messages: list[str]) -> list[dict]:
        if not markdown_messages:
            return []
        results: list[dict] = []

        async def callback(_socket, payload):
            if payload is None:
                for index, markdown in enumerate(markdown_messages):
                    results.append(await self.push_markdown(markdown))
                    if index + 1 < len(markdown_messages):
                        await asyncio.sleep(0.05)
                return "done"
            return None

        await self.online(callback, timeout=30)
        return results
