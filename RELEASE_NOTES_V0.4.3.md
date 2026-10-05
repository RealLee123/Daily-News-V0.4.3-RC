# Daily News V0.4.3 Product Optimization

## V0.4.3 重点

1. **QQ Markdown 标题超链接支持** —— 正式日报改用 QQ 原生 Markdown，标题写成 `[标题](canonical_url)`，手机上可直接点击跳转，正文不再显示长 URL。
2. **新闻简报格式优化** —— 固定板块结构（今日重点 / 中国 / 全球政治 / 金融市场 / 科技 / 其他），每条固定为「可点击标题 → 2 至 3 句摘要 → 来源」；新增 digest 临时排序 `importance × market impact × user relevance`，只影响当期排序、不写库。
3. **来源扩展** —— V0.4.2 引入的 Reuters / Bloomberg / White House 官方来源在本版完成端到端验证并可日常使用。
4. **稳定性提升** —— Breaking 判定统一走 `is_breaking_candidate()`；AI 异常分类与有限重试更明确；新增只读 `db-audit`；Preview 全链路只读并做 SHA-256 自证。
5. **增量处理优化** —— pending 拆成「待聚类文章 / 待重要性评估事件 / 待处理总计」；重复 URL/GUID/hash 不再重走 Embedding 与聚类。

## 范围

本版直接基于用户验收通过的 V0.4.2。未修改新闻采集架构、Article identity、Embedding 模型与持久化、Event 聚类、checkpoint、SQLite schema、Breaking 最终入口或 QQ API 协议。

## 新增

- `daily_news/delivery/qq_formatter.py`：Markdown 转 QQ 纯文本，以及板块优先、新闻条目优先的安全分段。
- QQBot `push_many_while_online()`：同一在线连接顺序发送多个纯文本段，底层消息格式仍为 `msg_type=0`。
- `qq-preview-test`：只读已有 Event，确定性生成带 `【Daily News V0.4.3 Preview Test】` 的简报，不创建 AI Provider、不写正式历史。
- `daily_news/audit.py` 与 `db-audit`：只读输出文章、Embedding、Event、正式历史和 pending 统计。
- `setup.cmd`、`run-news.cmd`、`qq-preview-test.cmd`、`verify.cmd` 四个 ASCII 日常入口；旧 CMD 保留。
- digest 临时排序：Event importance × market impact × user relevance；提高中国宏观/政策/市场与真正影响投资判断的金融事件优先级。

## 实际验证

- pytest：40 passed。
- QQ Preview：24 个既有 Event，4 个纯文本段；实际 QQ API 推送成功。
- Preview 数据库前后完全一致；Gemini 0 次、Embedding 0 次。
- 第一轮正常增量：434 条来源返回，420 条数据库命中，14 条新增，仅生成 14 个新 Embedding；1 个新 Event、6 个既有 Event 更新，AI 9 次且无失败。
- 紧接第二轮：434 条全部数据库命中，新增 0、Embedding 0、AI 0，耗时 5.6 秒。
- 数据库由 581 文章 / 578 Embedding / 90 Event 增量到 595 / 592 / 91；正式 delivery 和 digest history 均保持 0，pending 为 0。

## 数据库

没有 schema migration。交付保留同一份增量 `news.db`；`.env` 与真实密钥不进入 ZIP。
