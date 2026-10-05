# Daily-News V0.4.3 Release Checklist

> 本文件由 `publish-to-github.ps1` 自动生成，数据来自构建该发布包时的真实执行结果。
> 生成时间：2026-10-05 21:54:29
> 发布目录：`C:\Users\86152\Desktop\Daily-News-V0.4.3-Plus-Markdown-Brief\Daily-News-V0.4.3-Plus-Markdown-Brief\Daily-News-V0.4.3-RC`

---

## 1. 测试结果

```text
EXIT 0  127 passed, 1349 warnings in 11.27s   [wall 12.1 s]
```

原始输出尾部：

```text
============================== warnings summary ===============================
tests/test_incremental_resume.py: 780 warnings
tests/test_maintenance.py: 357 warnings
tests/test_official_sources_v042.py: 27 warnings
tests/test_postgres_migration.py: 5 warnings
tests/test_schedule_windows.py: 117 warnings
tests/test_sources_v041.py: 55 warnings
tests/test_storage.py: 8 warnings
  C:\Users\86152\Desktop\Daily-News-V0.4.3-Plus-Markdown-Brief\Daily-News-V0.4.3-Plus-Markdown-Brief\daily-news-python\.venv\Lib\site-packages\sqlalchemy\engine\default.py:1192: DeprecationWarning: The default datetime adapter is deprecated as of Python 3.12; see the sqlite3 documentation for suggested replacement recipes
    cursor.execute(statement, parameters)
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
127 passed, 1349 warnings in 11.27s
```

- 测试在源码目录执行（与发布目录代码完全一致），使用项目依赖环境。
- 发布包内不包含 `.venv`，使用者需先运行 `setup.cmd`。

## 2. 文件清理结果

发布目录共 **73** 个文件，只包含下列白名单内容：

- 代码：`daily_news/`（ai / collectors / delivery / pipeline / storage / jobs / audit）
- 配置：`config/sources.json`
- 测试：`tests/`
- 工具：`tools/`
- 文档：`README.md`、`RELEASE_NOTES*.md`、`QQ_MARKDOWN_PREVIEW_EXPERIMENT.md`、`docs/`
- 打包：`pyproject.toml`、`.env.example`、`.gitignore`、`.gitattributes`
- 一键入口：`setup.cmd`、`run-news.cmd`、`qq-preview-test.cmd`、`qq-markdown-preview-test.cmd`、`verify.cmd`、`3-news-ai-test-fixed.cmd`、`1-qq-smoke.cmd`、`2-qq-push-test.cmd`、`start-windows.cmd`

**已排除 / 未进入发布包：**

- `.env`  (1 KB)
- `.pytest_cache`  (dir)
- `.venv`  (dir)
- `0-填Gemini密钥.cmd`  (0.1 KB)
- `0-填QQ凭据.cmd`  (0.1 KB)
- `0-填密钥.cmd`  (0.1 KB)
- `0-填数据库连接串.cmd`  (0.1 KB)
- `0-验收V0.4.2.cmd`  (0.2 KB)
- `1-抓取QQ目标.cmd`  (0.2 KB)
- `3-测试新闻AI.cmd`  (1.1 KB)
- `4-跑两轮测试.cmd`  (0.2 KB)
- `5-数据库自检.cmd`  (0.2 KB)
- `daily_news_agent.egg-info`  (dir)
- `news.db`  (6744 KB)
- `output`  (dir)
- `set-database-url.ps1`  (3 KB)
- `set-gemini-key.ps1`  (1.8 KB)
- `set-qq-creds.ps1`  (2.2 KB)
- `verify_copy.db`  (2336 KB)

## 3. GitHub 上传前安全检查

```text
PASS - no .env / *.db in the publish copy; no key-, token-, openid-, phone- or email-shaped strings found in any file.

IGNORED - provably synthetic test fixtures / doc placeholders (6):
  IGNORED email address (password@ep-xxx-xxx.region.aws.neon.tech)  ->  daily_news\postgres.py
  IGNORED email address (pass@ep-cool-123.eu-central-1.aws.neon.tech)  ->  tests\test_postgres_migration.py
  IGNORED email address (pass@ep-cool-123.eu-central-1.aws.neon.tech)  ->  tests\test_postgres_migration.py
  IGNORED email address (sup3rsecret@ep-cool-123.aws.neon.tech)  ->  tests\test_postgres_migration.py
  IGNORED email address (pass@ep-cool-123.aws.neon.tech)  ->  tests\test_postgres_migration.py
  IGNORED email address (hunter2@ep-cool-123.aws.neon.tech)  ->  tests\test_postgres_migration.py
```

逐项结论：

| 检查项 | 结果 |
|---|---|
| Gemini Key（`AIza…`） | 未发现 |
| QQ AppSecret / AppID 值 | 未发现（只有 `.env.example` 中的空变量名） |
| QQ Token / Bearer 明文 | 未发现 |
| Cookie | 未发现 |
| OpenID | 未发现（仅 `news.db` 内 `settings.qq_target` 有，见下） |
| 数据库文件 | 发布包中不存在 |
| 手机号 / 邮箱 / 本机用户名路径 | 未发现 |
| `.env` 及其变体 | 发布包中不存在 |

> ⚠️ 源码目录中的 `news.db` 含 `settings.qq_target`（一条 C2C 目标记录，含你的 QQ openid）以及 595 篇文章 / 91 个事件。
> 它属于私人数据，**始终排除在发布包之外**；即使误入，也会被 `.gitignore` 的 `*.db` 拦下。

## 4. `.gitignore` 覆盖确认

以下内容不会进入 Git：

```text
.env  .env.*  .venv/  venv/  __pycache__/  *.py[cod]  .pytest_cache/
*.db  *.sqlite  *.log  output/  build/  dist/  *.zip
```

## 5. `git status` 检查

```text
would commit 73 files:
A  .env.example
A  .gitattributes
A  .github/workflows/digest.yml
A  .github/workflows/news-scan.yml
A  .gitignore
A  1-qq-smoke.cmd
A  2-qq-push-test.cmd
A  3-news-ai-test-fixed.cmd
A  QQ_MARKDOWN_PREVIEW_EXPERIMENT.md
A  README.md
A  RELEASE_NOTES.md
A  RELEASE_NOTES_V0.4.1.md
A  RELEASE_NOTES_V0.4.2.md
A  RELEASE_NOTES_V0.4.3.md
A  RELEASE_NOTES_V0.4.3_PLUS.md
A  RELEASE_NOTES_V0.4.md
A  config/sources.json
A  daily_news/__init__.py
A  daily_news/__main__.py
A  daily_news/ai/__init__.py
A  daily_news/ai/base.py
A  daily_news/ai/factory.py
A  daily_news/ai/gemini.py
A  daily_news/audit.py
A  daily_news/collectors/__init__.py
A  daily_news/collectors/bloomberg_latest.py
A  daily_news/collectors/public_page.py
A  daily_news/collectors/registry.py
A  daily_news/collectors/rss.py
A  daily_news/collectors/x_api.py
A  daily_news/config.py
A  daily_news/delivery/__init__.py
A  daily_news/delivery/qq.py
A  daily_news/delivery/qq_formatter.py
A  daily_news/delivery/qq_markdown.py
A  daily_news/jobs.py
A  daily_news/maintenance.py
A  daily_news/migrate.py
A  daily_news/models.py
A  daily_news/pipeline/__init__.py
A  daily_news/pipeline/article_identity.py
A  daily_news/pipeline/breaking.py
A  daily_news/pipeline/digest.py
A  daily_news/pipeline/event_clusterer.py
A  daily_news/pipeline/semantic.py
A  daily_news/postgres.py
A  daily_news/source_registry.py
A  daily_news/storage/__init__.py
A  daily_news/storage/database.py
A  docs/DETAILED_V0.4.3.md
A  docs/release-checklist-template.md
A  pyproject.toml
A  qq-markdown-preview-test.cmd
A  qq-preview-test.cmd
A  run-news.cmd
A  setup.cmd
A  start-windows.cmd
A  tests/test_ai_provider.py
A  tests/test_digest.py
A  tests/test_incremental_resume.py
A  tests/test_maintenance.py
A  tests/test_official_sources_v042.py
A  tests/test_pipeline.py
A  tests/test_postgres_migration.py
A  tests/test_qq_markdown_digest_v043plus.py
A  tests/test_qq_markdown_preview_tool.py
A  tests/test_qq_product_v043.py
A  tests/test_schedule_windows.py
A  tests/test_sources_v041.py
A  tests/test_storage.py
A  tools/__init__.py
A  tools/qq_markdown_preview.py
A  verify.cmd
```

## 6. 上传步骤

1. 在 GitHub 建一个**空仓库**（不要勾选 README / .gitignore / License）。
2. 在发布目录执行：

   ```powershell
   git remote add origin https://github.com/<你的账号>/<仓库名>.git
   git push -u origin main
   ```

3. 推完在仓库页面确认：没有 `news.db`、没有 `.env`、没有 `output/`。

## 7. 最终结论

这是个**纯整理版本**，不是开发版本：`daily_news/` 核心逻辑、SQLite schema、QQ 协议、采集与聚类流程一行未改，只做了发布前清理与文档整理。

| 发布门槛 | 结论 |
|---|---|
| 文件清理（无 `.env` / 无 `*.db` / 无日志 / 无临时脚本） | PASS |
| 安全检查（无密钥 / 无 Token / 无 Cookie / 无 OpenID） | PASS |
| `pytest -q` | PASS |
| `git status` 干净 | 见第 5 节 |

**总体判定：适合作为 GitHub 上的第一个 Release（V0.4.3）**，前提是第 3 节安全检查为 PASS、第 5 节 `git status` 无意外文件。
若第 1 节 pytest 未取得 `EXIT 0`，请先在本机双击 `verify.cmd` 确认通过后再推送。

建议发布时一并勾选 GitHub 的 “Create a release”，tag 用 `v0.4.3`，把本文件作为 Release 描述。

> 注意：本仓库默认不含 LICENSE。若想让别人能合法复用，发布前请自行选择并添加一个（如 MIT）。
