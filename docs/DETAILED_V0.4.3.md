# Daily News 私人新闻 Agent V0.4.3+（Markdown Brief）— 完整说明

> 本文件是 V0.4.3+ 交付时随包附带的完整说明，原文保留，作为 `README.md` 的详细附录。

V0.4.3+ 直接基于已经验收的 V0.4.3，把已验证成功的 QQ 原生 Markdown 能力接入日报展示层。新闻采集、Article identity、Embedding、Event 聚类、checkpoint、SQLite schema、QQ 鉴权/Gateway/发送端点均保持不变。

## V0.4.3 日常入口

普通使用只需要四个 ASCII 文件名：

- `setup.cmd`：创建 Python 3.12 虚拟环境、安装项目与 pytest、创建 `.env` 并显示配置状态。
- `run-news.cmd`：正常增量抓取、聚类、日报编辑与 QQ 推送；按北京时间自动选择早报或晚报。
- `qq-preview-test.cmd`：保留 V0.4.3 的纯文本兼容 Preview。
- `qq-markdown-preview-test.cmd`：只读当前数据库里已有的 Event，生成真实新闻简报样式的 Markdown Preview；Gemini/Embedding 调用均为 0，不写正式历史。
- `verify.cmd`：运行全部 pytest，再做只读数据库审计。

旧 CMD 全部保留，可继续使用。

## QQ Markdown Brief 与智能分段

正式早报/晚报使用 QQ 原生 Markdown。新闻标题统一写成 `[标题](canonical_url)`，正文不再显示长 URL；每条保持“可点击标题 → 2至3句摘要 → 来源”。`daily_news/delivery/qq_markdown.py` 负责格式校验和分段，原纯文本 Formatter 继续保留作为兼容能力。

默认单段安全上限为 1800 字符，可用 `QQ_MESSAGE_MAX_CHARS` 调整。分段先按完整板块，再按完整新闻条目；只有病态超长的单条内容才使用句子边界兜底。普通短日报仍一次发送。

## 日报产品排序

`daily_news/pipeline/digest.py` 在不修改 Event importance 的前提下增加临时 digest 排序：

`Event importance × market impact × user relevance`

- 中国宏观、央行、财政、房地产、产业、外贸、人民币、A股/港股及大型企业重大事件提高优先级。
- 央行、利率、通胀、非农、汇率、债券、股市、原油、黄金、大宗商品、重要财报和金融监管提高优先级。
- 普通社会、文化、体育事件不会仅因发生在中国就进入重点。
- 编辑结构固定为：今日重点、中国、全球政治、金融市场、科技、其他；空板块可省略，同一 Event 只出现一次。

这些分数只用于当期 digest 排序，不写数据库，不改变重大新闻判定。

## V0.4.1 已保留的小修

- pending 明确拆为：`待聚类文章`、`待重要性评估事件`、`待处理总计`，不再把文章与事件混成一个没有解释的数字。
- 所有最终 Breaking 判定统一调用 `daily_news.pipeline.breaking.is_breaking_candidate()`；测试报告、现有 breaking job 和事件统计使用同一可靠性门槛。

## Source Registry

`config/sources.json` 中每个来源具有：`source_id`、`source_name`、`source_type`、`source_tier`、`source_group`、`official`、`primary_source`、`default_weight`、`fetch_method`、`enabled`。支持 `news_media`、`official`、`social_primary`、`aggregator`。

`source_tier` 和权重只作为 AI 证据，不直接决定重要性。独立来源按 `source_group` 计数；White House 多栏目统一属于 `white_house`，不会机械放大确认数。

## V0.4 增量 checkpoint

- RSS item 入库后处于 `exact_deduped`；旧版 `pending` 状态继续兼容。
- 每个成功的 Embedding 批次立即写入文章行，保存向量、模型、维度和完成时间，状态进入 `embedded`。
- 灰区聚类、429、SSL EOF、timeout 或 5xx 随后失败时，已写入向量不会回滚；下轮直接从 `embedded` 继续。
- 文章关联 event 后进入 `clustered`；事件重要性沿用 `assessment_status=pending/retry/done` checkpoint。
- 重复 URL/GUID/hash 不会重新进入 Embedding 或聚类；同源 UPDATE/CORRECTED 仍走原来的文章家族更新流程。

SQLite 会在启动时对 V0.3.1 `news.db` 原地执行安全 migration，为 `article_versions_v2` 增加 `embedding`、`embedding_model`、`embedding_dimensions`、`embedded_at` 四列。不会清空文章、事件、QQ 设置或投递历史。

## 新闻流水线

1. 按 `config/sources.json` 扫描固定可信来源，不靠主题关键词发现新闻。
2. 用 canonical URL、RSS GUID/source ID、内容哈希做精确去重。
3. 识别同一来源的 UPDATE、CORRECTED、WRAPUP 等版本，归入同一文章家族。
4. 用 Gemini embedding 从最近事件召回最多 5 个候选：明确相同自动归并，明确不同新建事件，只有灰区才调用 Gemini 裁决。
5. 多媒体报道保留为同一 event 下的多篇 article；独立来源数、官方来源、首次/最后时间和 30 分钟增速进入事件证据。
6. Gemini 以 event 为单位返回结构化重要性判断，不使用关键词加分。
7. 早晚报记录已出现事件；同一事件下期只有经 Gemini 判断为“实质性新进展”才再次出现，并标成更新。

Gemini 发生 429、免费额度耗尽、超时或临时不可用时，文章仍会写入 SQLite。成功的 Embedding 批次立即落库，语义聚类保留为 `embedded`，事件重要性保留为 `retry`，下次运行自动继续。404、无效 Key 或配置/权限错误属于永久配置错误，不做无意义连续重试。

## 一次性运行本地真实测试

要求 Windows 上已有 Python 3.12（最低支持 3.11）。

1. 如果项目目录没有 `.env`，先复制 `.env.example` 为 `.env`。
2. 从 Google AI Studio 创建 Gemini API Key，只在本机 `.env` 中填写：

   ```ini
   AI_PROVIDER=gemini
   GEMINI_API_KEY=你的Key
   GEMINI_MODEL=gemini-3.5-flash-lite
   GEMINI_EMBEDDING_MODEL=gemini-embedding-001
   ```

3. 推荐日常使用新的 `setup.cmd`、`run-news.cmd`、`qq-preview-test.cmd`、`verify.cmd`。旧的 `3-news-ai-test-fixed.cmd` 与中文脚本仍保留。

如果 Windows 对中文文件名或代码页处理异常，可双击功能相同的 ASCII 版本 `3-news-ai-test-fixed.cmd`。

脚本在没有 `.venv` 时会自动创建并安装依赖，然后执行：多来源抓取 → 精确去重 → 同源版本处理 → embedding 事件聚类 → Gemini 重要性判断 → 测试版中文简报。结果同时打印到终端并保存到：

```text
output/新闻AI测试报告-YYYYMMDD-HHMMSS.md
```

报告和终端摘要包含 RSS 数、数据库命中、新增文章、复用/新生成 Embedding、新建/更新事件、灰区判断、重要性判断、pending、AI 调用/失败/重试次数及耗时。本命令的代码路径不创建 `QQBot`，不会向 QQ 发送内容。连续双击运行两次；第二轮 RSS 大量命中数据库时，`新生成 Embedding` 应显著下降或为 0。

## 当前来源与实际获取方式

| 来源 | Feed | 类型 |
|---|---|---|
| BBC World | BBC World RSS | 媒体 |
| BBC Business | BBC Business RSS | 媒体（与 BBC World 同组计票） |
| NHK News | NHK 综合新闻 RSS | 媒体 |
| UN News | 联合国新闻 RSS | 官方机构 |
| Federal Reserve | 美联储 Press Releases RSS | 官方机构 |
| Bank of Japan | 日本银行 What's New RSS | 官方机构 |
| Reuters | Reuters 官方公开 news sitemap | 高质量媒体；默认启用，只取标题、时间、URL |
| White House Briefings & Statements | whitehouse.gov 公开栏目页 | 官方第一手；默认启用 |
| White House Presidential Actions | whitehouse.gov 公开栏目页 | 官方第一手；默认启用 |
| White House Remarks | whitehouse.gov 公开栏目页 | 官方第一手；默认启用 |
| White House Releases | whitehouse.gov 公开栏目页 | 官方第一手；默认启用 |
| Bloomberg | Bloomberg 官方 `sitemaps/news/latest.xml` | 官方统一滚动新闻 discovery；默认启用，只取标题、发布时间和 URL |
| Donald Trump X | X API v2 用户时间线 | 默认关闭；需 `X_BEARER_TOKEN` |

Reuters 的 sitemap 是 Reuters 官方域名上的公开 SEO 发现端点，不是有服务承诺的 RSS/API；网站改版时可能变化。

Bloomberg `/latest` 的普通 HTTP 请求和云浏览器访问会进入 403 / “Are you a robot?” 页面，正常浏览器 headers 不能解决。Bloomberg 自己的公开 `robots.txt` 同时明确列出 `https://www.bloomberg.com/sitemaps/news/latest.xml`；本版实际请求该 XML 成功，内容覆盖 Bloomberg News、Opinion 等滚动条目，与 `/latest` 的统一 discovery 目标一致。Adapter 只读取 sitemap 中的 `news:title`、`news:publication_date`、`lastmod` 和文章 URL，不访问文章正文、不使用 Cookie/API Key/登录，也不触碰 paywall。它不是有服务承诺的 API；若返回 403、429、timeout、SSL 或 5xx，只记录来源错误，其他来源继续。

Trump X 继续只使用官方 X API v2。专项实测中，未登录的公开 profile 能在浏览器显示有限帖子，X Publish/oEmbed 也能生成 timeline 嵌入代码；但 oEmbed 只返回展示代码而不返回 post 数据，实际 timeline 由浏览器组件加载，相关公开 syndication 请求会限流，profile HTML 又是未文档化的内部 React 数据结构。因此它不满足长期无人值守后端接口的稳定性标准，本版没有实现 `TrumpXPublicAdapter`。不读取 Cookie、不登录、不接 Nitter/镜像；没有 `X_BEARER_TOKEN` 时该来源保持关闭并不影响其他来源。

可在 `.env` 单独开关：

```ini
REUTERS_ENABLED=true
BLOOMBERG_ENABLED=true
WHITE_HOUSE_ENABLED=true
TRUMP_X_ENABLED=false
X_BEARER_TOKEN=
```

任一来源失败只记录 `source fetch error`，其他来源继续执行。

## AI Provider 抽象

业务流水线只依赖 `daily_news.ai.base.AIProvider`。本版实现位于 `daily_news/ai/gemini.py`，工厂位于 `daily_news/ai/factory.py`。以后接 Groq 或其他模型时新增 adapter 并在 factory 注册，不需要改新闻聚类、简报或数据库逻辑。

Gemini 当前职责：

- embedding 语义召回；
- 灰区事件归并；
- event 重要性与 breaking 判断；
- 跨期“实质性新进展”判断；
- 最终中文简报编辑。

机械去重、文章家族和可靠性门槛不调用 LLM。

本版默认文本模型为 `gemini-3.5-flash-lite`。文本模型与 Embedding 模型是两套独立配置；Embedding 继续使用 `gemini-embedding-001`，不要将它改成 Flash-Lite。

为照顾 Free Tier，默认两次 AI 请求至少间隔 6 秒，并把单轮重要性评估和灰区裁决各限制为 12 次；未处理完的内容保留到下次运行。可在 `.env` 调整 `AI_MIN_CALL_INTERVAL_SECONDS`、`AI_MAX_ASSESSMENTS_PER_RUN` 和 `AI_MAX_CLUSTER_REVIEWS_PER_RUN`。

遇到 404 模型不可用、429 限流/额度耗尽、超时或 5xx 时，控制台会显示 Provider、实际模型、错误类型和处理建议，不再只抛出难读的 traceback。429、timeout、SSL/EOF/reset/broken pipe/disconnect/handshake 与 5xx 使用有限重试；404 和配置错误立即停止该请求并等待人工修正。已入库文章和 checkpoint 继续保留。

## 重大新闻可靠性门槛

AI 输出 `importance`、`breaking`、`scope`、`confidence`、`reason` 等结构化字段。即时候选还必须满足：

- `importance >= 80`；
- `confidence >= 0.80`；
- 至少 2 个独立来源，或至少 1 个官方来源；
- 事件未发送过同一 breaking alert。

人物社交账号只证明“人物公开说了什么”。Trump X 单独出现时不会被自动改写成政策已经生效；后续 White House、Reuters、Bloomberg 可以作为同一 Event 的不同 evidence 合并。

## 早晚报时间窗口

- 早报：前一天 21:30（含）→ 当天 09:30（不含）
- 晚报：当天 09:30（含）→ 当天 21:30（不含）

时间按 `.env` 中 `TIMEZONE=Asia/Shanghai` 计算，不再额外放宽 30 分钟。

## QQ 联调与 Preview

QQ Token、Gateway、消息 API 和目标类型没有变化。原 `msg_type=0` 纯文本 `push()` 保持原样；正式日报新增官方 `msg_type=2 + markdown.content` 路径，并在同一次上线连接中顺序发送安全分段。

下列旧入口继续保留：

- `0-填密钥.cmd`
- `1-qq-smoke.cmd`
- `2-qq-push-test.cmd`
- `set-secret.ps1`
- `start-windows.cmd`

原有命令仍为：

```powershell
python -m daily_news qq-smoke
python -m daily_news qq-push-test
python -m daily_news qq-preview-test
python -m daily_news qq-markdown-preview-test
```

生产新闻任务是单向广播，不提供私聊聊天服务。

## 本地命令与测试

```powershell
python -m daily_news init-db
python -m daily_news collect
python -m daily_news news-ai-test
python -m daily_news run-news
python -m daily_news qq-preview-test
python -m daily_news db-audit
python -m daily_news morning
python -m daily_news evening
python -m daily_news breaking

python -m pip install -e ".[dev]"
pytest
```

本版只声明 SQLite 所需依赖，不包含 PostgreSQL 驱动；也不包含 GitHub Actions 定时工作流。

## 文件与密钥安全

- `.env`、`.venv`、`output/`、缓存和测试环境不会进入发布 ZIP。
- 本次 V0.4.3 交付保留用户上传的 `news.db` 并在原库上完成增量验证，方便直接进行无 AI Preview；不会创建或替换为冷启动数据库。
- Gemini Key、QQ AppSecret 不得提交或发送。
- `.env.example` 只含变量名和空值。
- 如果密钥曾进入公开压缩包、截图或仓库历史，应立即在相应平台重置。
