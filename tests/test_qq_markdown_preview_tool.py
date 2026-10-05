from __future__ import annotations

import json
import sqlite3

from tools.qq_markdown_preview import PREVIEW_MARKDOWN, build_markdown_body, file_sha256, read_qq_target_read_only


def test_markdown_preview_payload_uses_official_shape():
    body = build_markdown_body()
    assert body["msg_type"] == 2
    assert body["markdown"]["content"] == PREVIEW_MARKDOWN
    assert "content" not in body
    assert "[美联储释放新的利率信号](https://www.reuters.com/markets/)" in PREVIEW_MARKDOWN


def test_target_reader_is_read_only(tmp_path):
    path = tmp_path / "news.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute(
        "INSERT INTO settings(key,value) VALUES('qq_target',?)",
        (json.dumps({"type": "c2c", "openid": "test-openid"}),),
    )
    connection.commit()
    connection.close()

    before = file_sha256(path)
    assert read_qq_target_read_only(path) == {"type": "c2c", "openid": "test-openid"}
    assert file_sha256(path) == before
