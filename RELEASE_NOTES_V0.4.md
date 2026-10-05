# Daily News V0.4 交付说明

## 本版修改文件

- `daily_news/ai/base.py`：Provider-neutral Embedding 批次 checkpoint 回调、重试计数、永久配置错误类型。
- `daily_news/ai/gemini.py`：保留 50 条/批、62 秒窗口与 429 部分成功；增加批次即时回调、有限网络重试；404/Key/权限错误不重试。
- `daily_news/ai/__init__.py`：导出永久配置错误类型。
- `daily_news/storage/database.py`：安全 SQLite migration、文章向量持久化、checkpoint 与 pending 统计。
- `daily_news/pipeline/event_clusterer.py`：只为缺失/失效向量调用 Provider，复用已落库向量，从灰区失败处续跑。
- `daily_news/jobs.py`：增量运行摘要与 Markdown 统计。
- `tests/test_incremental_resume.py`：新增断点续跑 A-E 和 V0.3.1 migration 测试。
- `tests/test_ai_provider.py`、`tests/test_pipeline.py`、`tests/test_storage.py`：适配并回归新 Provider 契约。
- `.env.example`：安全占位配置，不含密钥。
- `3-news-ai-test-fixed.cmd`、`README.md`、`pyproject.toml`、`daily_news/__init__.py`：V0.4 使用说明与版本号。

QQ 适配器及 `0/1/2` 已验证脚本、`set-secret.ps1`、`start-windows.cmd` 未修改。

## SQLite migration

启动时检查 `article_versions_v2`，缺少时原地添加：

| 列 | 用途 |
|---|---|
| `embedding` | JSON 编码的文章向量 |
| `embedding_model` | 生成向量的模型 |
| `embedding_dimensions` | 向量维度与有效性检查 |
| `embedded_at` | checkpoint 完成时间 |

文章阶段使用 `cluster_status`：`exact_deduped`（兼容旧 `pending`）→ `embedded` → `clustered`。事件阶段继续使用 `assessment_status`：`pending/retry` → `done`。migration 不删除或重建数据库。

## 本地测试

双击 `3-news-ai-test-fixed.cmd`。第一次处理新增内容；紧接着再双击一次，终端摘要中的“数据库已有/精确重复”应上升，“新生成 Embedding”应显著下降或为 0。

自动测试覆盖：Embedding 后聚类失败复用、SSL EOF 续跑、120 篇部分 429 checkpoint、旧 RSS item 跳过、同源实质更新关联、旧 schema 原地升级。
