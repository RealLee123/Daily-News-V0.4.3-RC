# Release Notes

当前版本：**V0.4.3**

| 版本 | 主题 |
|---|---|
| **V0.4.3**（当前） | QQ Markdown 简报、产品排序、只读审计、ASCII 一键入口 |
| V0.4.2 | 官方来源：Reuters / Bloomberg / White House |
| V0.4.1 | pending 计数拆解、breaking 判定统一入口 |
| V0.4 | 增量 checkpoint、Embedding 持久化、断点续跑 |
| V0.3.x | Gemini Provider、事件聚类、早晚报 |

---

## 未发布 / Unreleased

以下变化在 V0.4.3 之后完成，尚未归入某个正式版本号。此前的版本记录保持原样。

### 数据库：PostgreSQL（Neon）生产化

- 生产库由 SQLite 迁移到 **PostgreSQL（Neon）**，新增 `setup-postgres` / `db-test` / `migrate-db` 三个命令。
- `migrate-db` 把既有 `news.db` 全量搬到 PostgreSQL：源库以 `mode=ro` 打开（物理只读），搬完逐表比对行数。实测迁移 1489 行、8 张表逐表一致，源库文件哈希前后未变。
- `DATABASE_URL` 不再有 SQLite 兜底：未配置时命令直接提示去跑 `setup-postgres`，不会悄悄写到别处。
- 驱动固定在连接串里（`postgresql+psycopg2://`），避免裸 `postgresql://` 随 SQLAlchemy 版本在 psycopg2 / psycopg3 之间漂移导致的 `ModuleNotFoundError`。
- SQLite 仅保留两个用途：本地备份，以及一次性迁移的来源；各命令仍可用 `--database-url sqlite:///news.db` 显式跑本地库。
- 已在真实 Neon 上验证：读写路径、类型往返（`timestamptz` / `boolean` 读回的是原生类型而非字符串）、Embedding 完整性、逐表行级指纹一致。

### 修复

- **`db-audit` 的 Embedding 误报**：审计原先要求「有向量的行数 ≤ 最新版本数」。但同源文章出过新版本后，旧版本被标 `is_latest=false` 却保留向量，该不等式会合法地不成立，导致审计永久误报。现改为与文章总行数比较（与其相邻的 Event 关联检查保持同一口径）。
- **日报重复执行不再报错退出**：`morning` / `evening` 的幂等检查原先排在事件筛选之后，重复运行会因「候选事件为 0」抛异常——不会重复推送，但会在调度日志里留下 traceback 和非零退出码。检查已提前，重复执行现在是安静的空操作（打印「本期已经推送，跳过重复发送」并以 0 退出）。

### 改进

- **`news-scan` / `collect` 增加阶段进度输出**：抓取（含每个来源的条数）、Embedding、聚类、AI 评估各阶段各输出一行，扫描不再长时间静默。仅为新增日志，未改动采集、去重、Embedding、聚类、Prompt 或推送逻辑。

### 清理

- 清理了把生产库描述成 SQLite / `news.db` 的过时文案（`daily_news/jobs.py`、`daily_news/maintenance.py`、`README.md`、`docs/DETAILED_V0.4.3.md`、`qq-markdown-preview-test.cmd`）。历史版本记录、迁移源说明、以及描述 SQLite 自身行为的技术注释保持不变。

### 测试

- 新增 2 个日报幂等测试：首跑写入投放记录与 digest 历史；重复执行安静退出且零写入。
- 当前测试数：**127 passed**。

---

## V0.4.3

本版直接基于用户验收通过的 V0.4.2。**未修改**新闻采集架构、Article identity、Embedding 模型与持久化、Event 聚类、checkpoint、SQLite schema、Breaking 最终入口或 QQ API 协议。

### 重点

**0. 数据库维护模块（新增）**

- 新增 `daily_news/maintenance.py`，导出 `biweekly_cleanup()`，为长期无人值守运行提供可重复执行的保留策略清理。
- 可用 `python -m daily_news db-maintenance [--dry-run] [--vacuum] [--json]` 调用，或走等价的 `python -m daily_news.maintenance`。
- 未改表结构、未加字段；所有语句只使用现有 schema 的列，且布尔与时间一律走绑定参数，因此同一份 SQL 在 SQLite 与 PostgreSQL 上都成立。
- 保留策略（个人简报口径，非档案库）：Article 14 天且无 Event 引用（不看 `is_latest`，当前版本与历史版本一视同仁）；Event 28 天（连同 `event_articles_v2` 关联一并删除）；AI 日志 90 天；delivery / digest 历史 30 天；Embedding 跟随所属 Article / Event 删除，并默认清理流水线永不再读的向量——只整组置空，从不改写或刷新向量值。
- 详见 README「数据库维护」一节。

**运行调度（两小时扫描 + 早晚报只展示）**
- 新增两小时扫描入口 `python -m daily_news news-scan`（= 采集 + 去重 + Embedding + 聚类 + 重要性判断 + 重大新闻即时推送），`breaking` 保留为等价别名。
- 早报/晚报不再采集新闻：只读取各自 12 小时窗口（早报 昨天21:30→今天09:30，晚报 今天09:30→今天21:30）内已处理好的 Event，交给 AI 编辑后发送。新闻处理全部由两小时扫描提前完成。
- 查询窗口的时间边界统一转为 UTC 再入库（`jobs._as_db_time()`）：库里的时间戳是 `+00:00` 字符串而 SQLite 按文本比较，此前用本地 `+08:00` 边界会让窗口整体偏移 8 小时，`breaking` 因此从未真正命中过事件。
- 重大新闻提醒的池窗口由 3 小时放宽到 6 小时（`BREAKING_POOL_HOURS`）且每轮最多推 3 条（`BREAKING_MAX_ALERTS_PER_RUN`）：原实现在推完一条后即返回，2 小时频次下会把排在后面的重大事件永久漏掉。

**1. QQ Markdown 标题超链接支持**

正式早报 / 晚报改用 QQ 原生 Markdown（`msg_type=2` + `markdown.content`）。每条新闻标题写成 `[标题](canonical_url)`，在手机上可直接点击跳转到原文，正文不再出现一长串 URL。这是本版用户可见的最大变化。

**2. 新闻简报格式优化**

- 每条固定为「可点击标题 → 2 至 3 句摘要 → 来源」，不输出 AI 评论或后台指标。
- 板块结构固定：今日重点 / 中国 / 全球政治 / 金融市场 / 科技 / 其他；空板块省略，同一 Event 只出现一次。
- 新增 digest 临时排序：`Event importance × market impact × user relevance`，提高中国宏观 / 政策 / 市场与真正影响投资判断的金融事件优先级。该分数只用于当期排序，不写数据库，不改变重要性判定。
- 超长内容只在**完整板块**或**完整新闻条目**之间切分，不截断标题、摘要或 Markdown 链接；`daily_news/delivery/qq_markdown.py` 负责格式校验与分段，默认单段上限 1800 字符（`QQ_MESSAGE_MAX_CHARS`）。原纯文本 formatter 与其分段能力保留为兼容路径。

**3. 来源扩展**

V0.4.2 引入的官方来源在本版完成端到端验证并可日常使用：Reuters（官方 news sitemap）、Bloomberg（官方 `sitemaps/news/latest.xml`）、White House 四个公开栏目页（Briefings & Statements / Presidential Actions / Remarks / Releases）。来源清单统一由 `config/sources.json` 描述（`source_id` / `source_tier` / `source_group` / `official` / `fetch_method` / `enabled`），独立来源按 `source_group` 计票，White House 多栏目不会机械放大确认数。

**4. 稳定性提升**

- 所有最终 Breaking 判定统一走 `daily_news.pipeline.breaking.is_breaking_candidate()`，测试报告、breaking job、事件统计使用同一可靠性门槛。
- AI 异常处理更明确：控制台显示 Provider、实际模型、错误类型与处理建议；429 / timeout / SSL-EOF / 5xx 有限重试，404 与配置错误立即停止该请求等人工修正，已入库文章与 checkpoint 全部保留。
- `daily_news/audit.py` 与 `db-audit`：只读输出文章、Embedding、Event、正式历史与 pending 统计。
- Preview 全链路只读：发送前后对 `news.db` 做 SHA-256 比对，Gemini 与 Embedding 调用均为 0，不写 delivery / digest 历史。
- 新增四个 ASCII 日常入口 `setup.cmd` / `run-news.cmd` / `qq-preview-test.cmd` / `verify.cmd`，避免中文 `.cmd` 在 cmd.exe 下的代码页问题。

**5. 增量处理优化**

- pending 明确拆成「待聚类文章」「待重要性评估事件」「待处理总计」三个数，不再混成一个没有解释的数字。
- 重复 URL / GUID / hash 不再重新进入 Embedding 或聚类；成功的 Embedding 批次立即落库并进入 `embedded`，事件重要性沿用 `assessment_status = pending / retry / done` checkpoint。

### 实际验证

- pytest：45 passed。
- QQ Markdown Preview：24 个既有 Event，4 个 Markdown 分段（1592 / 1689 / 1185 / 965 字符），QQ 原生 Markdown API 实际推送成功；Gemini 0 次、Embedding 0 次，`news.db` 文件哈希前后一致。
- QQ 纯文本 Preview：24 个既有 Event，4 个纯文本段，实际推送成功，数据库前后完全一致。
- 第一轮正常增量：434 条来源返回，420 条数据库命中，14 条新增，仅生成 14 个新 Embedding；1 个新 Event、6 个既有 Event 更新，AI 9 次且无失败。
- 紧接第二轮：434 条全部数据库命中，新增 0、Embedding 0、AI 0，耗时 5.6 秒。
- 数据库由 581 文章 / 578 Embedding / 90 Event 增量到 595 / 592 / 91；正式 delivery 与 digest history 均保持 0，pending 为 0。

### 数据库

没有 schema migration。本次交付保留同一份增量 `news.db`；`.env` 与真实密钥不进入发布包。

### 说明

本版只声明 SQLite 所需依赖，不包含 PostgreSQL 驱动；也不包含 GitHub Actions 定时工作流。
