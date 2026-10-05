# Daily News V0.4.1 Stable Incremental

## 修改文件

- `config/sources.json`：正式 Source Registry 与四类来源配置。
- `daily_news/source_registry.py`：来源 metadata、开关和旧配置兼容。
- `daily_news/collectors/registry.py`：统一调度、单来源失败隔离。
- `daily_news/collectors/rss.py`：RSS 适配到统一 SourceDefinition。
- `daily_news/collectors/public_page.py`：Reuters 官方 news sitemap、Bloomberg/White House 公开页 metadata 适配。
- `daily_news/collectors/x_api.py`：`@realDonaldTrump` 官方 X API v2 可选适配器。
- `daily_news/models.py`、`daily_news/storage/database.py`：持久化 source tier、primary source 与默认权重；pending 分项统计。
- `daily_news/pipeline/breaking.py`：唯一 Breaking 最终判定入口。
- `daily_news/pipeline/event_clusterer.py`、`daily_news/pipeline/digest.py`、`daily_news/ai/gemini.py`：将 source metadata 和 attribution 语义送入现有 Event pipeline。
- `daily_news/jobs.py`：来源收集、pending 分项和统一 Breaking 入口。
- `daily_news/config.py`、`.env.example`：来源独立开关与空 X Token。
- `tests/test_sources_v041.py`：来源合并、归因、分组、去重、失败隔离、无凭据降级、pending、Breaking 和适配器测试。
- `README.md`、两个新闻 AI CMD、版本文件：V0.4.1 说明。

QQ Bot 适配器和原有 QQ smoke/push 脚本未修改。

## 实际来源状态

- Reuters：官方公开 news sitemap；本次实测返回有效文章 metadata。只读取标题、时间和 URL，不抓正文。
- Bloomberg：官方公开 `/latest` 页面 adapter；本次自动访问实测 HTTP 403，因此默认关闭。没有伪造 RSS、第三方镜像或绕过。
- White House：官网 Briefings & Statements、Presidential Actions、Remarks、Releases 四个公开栏目；同属 `source_group=white_house`。
- Trump X：官方 X API v2 `users/by/username` + `users/:id/tweets`；需要 Bearer Token，默认关闭。

## SQLite migration

V0.4 数据库原地新增 `source_tier`、`primary_source`、`default_weight` 三列。旧数据使用安全默认值；文章、事件、Embedding、QQ 设置与投递历史不删除。
