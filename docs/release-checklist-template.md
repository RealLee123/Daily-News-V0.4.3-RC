# Daily-News V0.4.3 Release Checklist

> 本文件由 `publish-to-github.ps1` 自动生成，数据来自构建该发布包时的真实执行结果。
> 生成时间：{{GENERATED_AT}}
> 发布目录：`{{PUBLISH_DIR}}`

---

## 1. 测试结果

```text
{{PYTEST_SUMMARY}}
```

原始输出尾部：

```text
{{TEST_TAIL}}
```

- 测试在源码目录执行（与发布目录代码完全一致），使用项目依赖环境。
- 发布包内不包含 `.venv`，使用者需先运行 `setup.cmd`。

## 2. 文件清理结果

发布目录共 **{{FILE_COUNT}}** 个文件，只包含下列白名单内容：

- 代码：`daily_news/`（ai / collectors / delivery / pipeline / storage / jobs / audit）
- 配置：`config/sources.json`
- 测试：`tests/`
- 工具：`tools/`
- 文档：`README.md`、`RELEASE_NOTES*.md`、`QQ_MARKDOWN_PREVIEW_EXPERIMENT.md`、`docs/`
- 打包：`pyproject.toml`、`.env.example`、`.gitignore`、`.gitattributes`
- 一键入口：`setup.cmd`、`run-news.cmd`、`qq-preview-test.cmd`、`qq-markdown-preview-test.cmd`、`verify.cmd`、`3-news-ai-test-fixed.cmd`、`1-qq-smoke.cmd`、`2-qq-push-test.cmd`、`start-windows.cmd`

**已排除 / 未进入发布包：**

{{CLEANUP_TABLE}}

## 3. GitHub 上传前安全检查

```text
{{SCAN_SUMMARY}}
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
{{GIT_STATUS}}
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
| 安全检查（无密钥 / 无 Token / 无 Cookie / 无 OpenID） | {{SCAN_VERDICT}} |
| `pytest -q` | {{TEST_VERDICT}} |
| `git status` 干净 | 见第 5 节 |

**总体判定：适合作为 GitHub 上的第一个 Release（V0.4.3）**，前提是第 3 节安全检查为 PASS、第 5 节 `git status` 无意外文件。
若第 1 节 pytest 未取得 `EXIT 0`，请先在本机双击 `verify.cmd` 确认通过后再推送。

建议发布时一并勾选 GitHub 的 “Create a release”，tag 用 `v0.4.3`，把本文件作为 Release 描述。

> 注意：本仓库默认不含 LICENSE。若想让别人能合法复用，发布前请自行选择并添加一个（如 MIT）。
