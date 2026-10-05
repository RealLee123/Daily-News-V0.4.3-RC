# Daily News · AI 驱动的私人新闻 Agent

**Version: V0.4.3**

Daily-News 是一个 AI 驱动的新闻聚合系统。它跑在你自己的电脑上，定时扫描一批固定的可信来源，去掉重复、把讲同一件事的多篇报道聚成一个事件，交给 Gemini 判断重要性与是否突发，最后编辑成一份中文简报，通过 QQ 机器人推到你手机。

不用关键词猜新闻，也不做聊天——它只回答一个问题：**今天有什么值得看。**

## 功能

- **多源新闻采集** — RSS、官方新闻 sitemap、政府公开栏目页，来源写在 `config/sources.json` 里
- **文章去重** — canonical URL / RSS GUID / 内容哈希精确去重，并识别同源的 UPDATE、CORRECTED 版本
- **Embedding 语义聚类** — 向量召回候选，明确相同自动归并、明确不同新建事件，**只有灰区才调用 LLM**
- **Event 事件生成** — 以事件为单位汇总多篇报道，统计独立来源数、官方来源、首次/最后时间
- **AI 新闻简报** — Gemini 返回结构化的重要性 / breaking 判断，编辑成中文早晚报
- **QQ 机器人推送** — QQ 原生 Markdown，标题即超链接，超长自动按板块 / 条目分段

## 架构

```
RSS / Sitemap / Public Page
        ↓
      Article          （精确去重 + 同源版本合并）
        ↓
     Embedding         （向量落库，增量持久化）
        ↓
   Event Cluster       （语义召回，灰区才调 LLM 裁决）
        ↓
     AI Digest         （事件重要性 + 中文简报编辑）
        ↓
  QQ Markdown Push     （原生 Markdown，自动分段）
```

整条链路**增量、可断点续跑**：每个成功的 embedding 批次立即写入文章行，AI 出现 429 / 超时 / 5xx 时只把当前这一步保留为 `retry`，已入库的文章与 checkpoint 下次继续用，不会重算。

每一步在做什么：

1. **抓取** — 按 `config/sources.json` 扫描固定来源，不靠主题关键词发现新闻。
2. **精确去重** — canonical URL、RSS GUID / source ID、内容哈希。
3. **同源版本合并** — UPDATE / CORRECTED / WRAPUP 归入同一「文章家族」，只保留最新版本。
4. **语义聚类** — 用 embedding 从近期事件召回最多 5 个候选。
5. **事件重要性** — 事件级结构化判断，不用关键词加分。
6. **编辑推送** — 固定板块：今日重点 / 中国 / 全球政治 / 金融市场 / 科技 / 其他。
7. **跨期去重** — 同一事件下期只有被判为「实质性新进展」才再次出现，并标成更新。

**Breaking 可靠性门槛**（四条同时满足才算突发）：`importance ≥ 80` 且 `confidence ≥ 0.80`；至少 2 个独立来源或至少 1 个官方来源（同一 `source_group` 不重复计票）；该事件此前没发过同一条 breaking。人物社交账号只证明「这个人公开说了什么」，单独出现不会改写成「政策已生效」。

## 本地运行

要求 Python 3.11+（推荐 3.12）。整套东西只依赖本机，**不需要任何云服务或部署**。

### 1. 安装环境

双击 **`setup.cmd`** —— 建虚拟环境、装依赖，并从 `.env.example` 生成 `.env`。

（等价手动操作：）

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
copy .env.example .env
```

### 2. 配置 `.env`

至少填一把 Gemini Key（[Google AI Studio](https://aistudio.google.com/apikey) 免费申请）：

```ini
AI_PROVIDER=gemini
GEMINI_API_KEY=你的Key
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_EMBEDDING_MODEL=gemini-embedding-001
```

想推到 QQ，再补 `QQ_APP_ID` / `QQ_APP_SECRET`。常用开关：

| 变量 | 说明 | 默认 |
|---|---|---|
| `GEMINI_API_KEY` | Gemini Key（必填） | 空 |
| `GEMINI_MODEL` / `GEMINI_EMBEDDING_MODEL` | 文本模型 / 向量模型（**勿把向量模型换成 Flash**） | `gemini-3.5-flash-lite` / `gemini-embedding-001` |
| `EMBEDDING_DIMENSIONS` | 向量维度 | `512` |
| `AI_MIN_CALL_INTERVAL_SECONDS` | 两次 AI 请求最小间隔（免费档限流） | `6` |
| `DATABASE_URL` | 数据库地址（**生产库是 PostgreSQL**，用 `setup-postgres` 写入） | 空（不配置就提示你先配置） |
| `TIMEZONE` / `MORNING_TIME` / `EVENING_TIME` | 时区与早晚报分界 | `Asia/Shanghai` / `09:30` / `21:30` |
| `QQ_APP_ID` / `QQ_APP_SECRET` | QQ 机器人凭据 | 空 |
| `QQ_MESSAGE_MAX_CHARS` | 单段消息上限（超出自动分段） | `1800` |
| `REUTERS_ENABLED` / `BLOOMBERG_ENABLED` / `WHITE_HOUSE_ENABLED` | 官方来源开关 | `true` |
| `TRUMP_X_ENABLED` / `X_BEARER_TOKEN` | X 时间线来源 | `false` / 空 |

免费档默认两次 AI 请求间隔 6 秒，单轮评估 / 裁决各限 12 次；没处理完的内容留到下一轮，不会被丢。

### 3. 运行

```powershell
python -m daily_news run-news                      # 正式增量运行（早/晚报自动选择，会推送）
python -m daily_news news-ai-test                  # 完整流水线 + 测试简报（不发 QQ）
python -m daily_news collect                       # 只抓取 + 去重
python -m daily_news qq-markdown-preview-test      # 只读当前数据库，生成 Markdown 简报样例
python -m daily_news db-audit                      # 只读数据库审计
python -m daily_news db-maintenance --dry-run      # 数据库维护，只统计不修改
python -m daily_news db-maintenance                # 数据库维护，实际执行
python -m pip install -e ".[dev]"; pytest          # 跑测试
```

## 数据库（PostgreSQL）

生产库是 **PostgreSQL（Neon）**；SQLite 只保留两个用途：本地备份，以及一次性迁移的来源。
没有配置 `DATABASE_URL` 时命令会直接告诉你去跑 `setup-postgres`，**不会**悄悄退回 `sqlite:///news.db`。

```powershell
python -m daily_news setup-postgres     # 粘贴 Neon 连接串，写入 .env（其它配置原样保留）
python -m daily_news db-test            # 连上去 → 建 schema → 报告表数量与行数
python -m daily_news migrate-db         # 把 news.db 全量搬过去（源库只读，不会被动）
python -m daily_news migrate-db --force # 目标库非空时，先清空目标表再搬
```

几个要点：

- `db-test` 会执行 `Database.initialize()`，所以**全新数据库不需要手工建表**。
- `migrate-db` 的源库以 `mode=ro` 打开，物理上无法写入；搬完会逐表比对行数，不一致就报错。
- 目标库已有数据时 `migrate-db` 会**拒绝执行**并列出各表行数，确认要覆盖再加 `--force`。
- 驱动固定在连接串里（`postgresql+psycopg2://`）：裸写 `postgresql://` 在 SQLAlchemy 2.0 走 psycopg2、2.1 走 psycopg3，容易莫名其妙报 `ModuleNotFoundError`。
- 连接串只以脱敏形式打印（`user:***@host/db`），密码不会进日志。

本地仍想用 SQLite 时显式指定即可，所有命令都支持：

```powershell
python -m daily_news db-audit --database-url sqlite:///news.db
```

等价的 Windows 一键入口：`setup.cmd`、`run-news.cmd`、`qq-preview-test.cmd`、`qq-markdown-preview-test.cmd`、`verify.cmd`、`3-news-ai-test-fixed.cmd`、`1-qq-smoke.cmd`、`2-qq-push-test.cmd`。

只在本机看效果、不真的推送：双击 **`qq-markdown-preview-test.cmd`** —— 只读现有 Event 生成样例并发出，不调 Gemini、不生成 embedding、不写库（发送前后对数据库做快照比对自证）。

想确认代码没问题：双击 **`verify.cmd`** —— 先跑全部 pytest，再做只读数据库审计。

## 运行调度

Daily-News 的定位是「**两小时新闻处理 + 早晚日报 + 重大新闻即时提醒**」——不等到日报时才一次性处理一大批新闻，而是每两小时就把抓取、去重、Embedding、Event 聚类、重要性判断全部做完。

| 任务 | 命令 | 频率 | 作用 |
|---|---|---|---|
| 新闻扫描 | `python -m daily_news news-scan` | 每 2 小时（:20） | 抓取 → 入库 → 去重 → Embedding → 聚类 → 重要性判断 → **重大新闻即时推送** |
| 只处理不推送 | `python -m daily_news collect` | 按需 | 同上但不发 QQ（不需要 QQ 凭据） |
| 早报 | `python -m daily_news morning` | 每天 09:30 | 读取窗口内的 Event，AI 编辑后推送 |
| 晚报 | `python -m daily_news evening` | 每天 21:30 | 同上，晚报 |

（`breaking` 是 `news-scan` 的旧名字，两者行为完全相同。）

职责是分开的：**扫描任务负责处理**（采集、去重、Embedding、聚类、重要性判断），**日报只负责展示**。

- 扫描是**增量**的：重复 URL 不会重走 Embedding，已有向量直接复用，已评估的 Event 不会重新调 AI，所以稳态下每轮几秒完成。
- 日报**不采集、不生成 Embedding、不重新聚类**，只把时间窗口内的 Event 取出来交给 AI 编辑。两小时扫描已经把新闻处理提前做完，09:30 / 21:30 那一刻只剩排版和发送。

| 期次 | 窗口 |
|---|---|
| 早报 09:30 | 昨天 21:30 → 今天 09:30 |
| 晚报 21:30 | 今天 09:30 → 今天 21:30 |

两期窗口首尾相接、各 12 小时，所以早晚报不会互相重复。

### 交给 GitHub Actions 时的注意点

GitHub Actions 的 cron 走 **UTC**。`Asia/Shanghai` 是 UTC+8，偶数偏移，所以两小时扫描的分钟数不需要换算：

```yaml
# 新闻扫描，每 2 小时（UTC 00:20 / 02:20 ... = 北京时间同分钟）
- cron: "20 */2 * * *"
# 早报 09:30 CST = 01:30 UTC
- cron: "30 1 * * *"
# 晚报 21:30 CST = 13:30 UTC
- cron: "30 13 * * *"
```

08:20 与 20:20 两轮**必须保留**：它们分别是 09:30 早报和 21:30 晚报之前最后一轮扫描。

两个运行约束：

- **数据库里的时间戳一律是 UTC**。给查询传时间边界前必须先转 UTC（`jobs._as_db_time()`）：SQLite 按文本比较，带 `+08:00` 的边界会比库里的值“晚” 8 小时，窗口直接失效；PostgreSQL 按时刻比较本不受影响，但统一走 UTC 才能让两个后端行为一致。
- 扫描和日报可以重叠运行：PostgreSQL 支持并发写，不再有 SQLite 那种单写者抢锁的问题。但仍建议同一个任务不要重叠启动（例如上一轮扫描没跑完又起一轮），否则会重复消耗 AI 调用。

## 数据库维护

长期无人值守运行时，数据库会一直长。`daily_news/maintenance.py` 提供一个可反复执行的保留策略清理（`biweekly_cleanup()`），只删除流水线已经读不到的数据，不改表结构、不加字段。

```powershell
python -m daily_news db-maintenance --dry-run          # 只显示预计删除/清理数量
python -m daily_news db-maintenance                    # 实际执行
python -m daily_news db-maintenance --vacuum           # 执行并主动回收空间（VACUUM）
python -m daily_news db-maintenance --json             # 额外输出 JSON 摘要
python -m daily_news db-maintenance --database-url sqlite:///news.db   # 指定数据库
python -m daily_news db-maintenance --no-compact-orphan-embeddings     # 只做随行删除，保留不可达向量
python -m daily_news.maintenance --help                # 等价的独立入口，含全部参数
```

这是个**个人简报**的保留策略，不是历史档案库：

| 对象 | 规则 |
|---|---|
| Article | `published_at` 早于 14 天 **且** 没有被任何 Event 引用。**不看 `is_latest`**：当前版本和历史版本一视同仁，新闻的重要性由 Event 负责；被 Event 引用的永不删除 |
| Event | `last_seen` 早于 28 天即删（纯按时间，没有重要性 / breaking 豁免）；删除时**同步删除它的 `event_articles_v2` 关联**，不留孤儿 |
| Embedding | 与所属 Article / Event 同行，删行即删向量；默认还会清掉流水线永不再读的向量（历史版本 / 已 `clustered`）。清理只把 `embedding` / `embedding_model` / `embedding_dimensions` / `embedded_at` 四列**整组置空**，从不改写、重算或刷新向量值，因此不可能触发重新生成。用 `--no-compact-orphan-embeddings` 可只做随行删除 |
| AI 日志 | `ai_decisions_v2`、`ai_failures_v3` 超过 90 天清理 |
| Delivery / digest 历史 | 超过 30 天清理 |

删除顺序是 **Event（含关联）→ Article → 孤立 Embedding → AI 日志 → 投递历史**：先让过期 Event 释放它持有的 Article 关联，同一轮里那些 Article 才可能被判定为无引用。

生命周期就是你说的那条链：Event 保留 28 天 → 解除 Article 引用 → Article 满 14 天后自动清理。

注意：删除只是把空间标记为可复用，数据库不会立刻变小（PostgreSQL 由后台 autovacuum 慢慢回收）。加 `--vacuum` 会额外执行一次 VACUUM 主动回收。不加也能防止空间无限增长，因为新数据会复用这些空间。

## 来源

| 来源 | 获取方式 | 类型 |
|---|---|---|
| BBC World / BBC Business | RSS | 媒体（同组计票） |
| NHK News | RSS | 媒体 |
| UN News / Federal Reserve / Bank of Japan | RSS | 官方机构 |
| Reuters | 官方公开 news sitemap | 高质量媒体（只取标题 / 时间 / URL） |
| Bloomberg | 官方 `sitemaps/news/latest.xml` | 媒体 discovery（只取 metadata，不抓正文） |
| White House ×4 栏目 | whitehouse.gov 公开栏目页 | 官方第一手（4 栏目同属一家） |
| Donald Trump X | X API v2 用户时间线 | 默认关闭，需 `X_BEARER_TOKEN` |

任一来源失败只记录 `source fetch error`，其它来源继续。

Reuters 的 sitemap 是官方域名上的公开 SEO 发现端点，Bloomberg 的 `/latest.xml` 是它自己 `robots.txt` 里列出的端点——**两者都不是有服务承诺的 API**；adapter 只读 XML 里的标题 / 时间 / URL，不访问正文、不用 Cookie / Key / 登录、不碰 paywall。

## 项目结构

```
daily-news/
├─ daily_news/
│  ├─ ai/          # AIProvider 抽象 + Gemini 实现 + factory
│  ├─ collectors/  # RSS / news_sitemap / public_page / x_api 采集器
│  ├─ pipeline/    # 去重、文章家族、语义聚类、breaking、digest 编辑
│  ├─ delivery/    # QQBot（token / gateway / 发送）、纯文本与 Markdown 分段
│  ├─ storage/     # SQLAlchemy 数据层：schema 定义、原地 migration、各类查询
│  ├─ jobs.py      # 各命令的任务实现
│  └─ audit.py     # 只读审计
├─ config/sources.json   # 来源清单（含 tier / group / 开关）
├─ tests/                # pytest
├─ tools/                # qq_markdown_preview 等独立小工具
├─ docs/DETAILED_V0.4.3.md
├─ pyproject.toml
└─ .env.example
```

## 安全

- 密钥只从环境变量 / `.env` 读取，**代码里没有任何硬编码凭据**。
- `.env`、`.venv/`、`*.db`、`output/`、缓存均已被 `.gitignore` 排除。
- `.env.example` 只含变量名和空值。
- 生产数据（新闻历史 + `settings.qq_target` 里你的 QQ openid）现在存在 Neon；本地 `news.db` 是它的备份。两者连同 `.env` 里的 Neon 连接串（全权限凭据）都是**你的私人数据**，永远不要提交。
- 如果某个 Key 曾经进过公开仓库 / 压缩包 / 截图，请立即在对应平台重置。

## 版本

V0.4.3 → 见 [RELEASE_NOTES.md](RELEASE_NOTES.md)；V0.4.3 的完整设计说明见 [docs/DETAILED_V0.4.3.md](docs/DETAILED_V0.4.3.md)。

## 说明

本项目是**单向广播**的私人工具，不提供私聊聊天服务。采集的均为各站点公开的 RSS / sitemap / 公开页面，请自行遵守各来源的使用条款。
