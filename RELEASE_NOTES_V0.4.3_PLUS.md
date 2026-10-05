# Daily News V0.4.3+ Markdown Brief

本版只优化日报展示层，没有修改采集、Embedding、事件聚类、checkpoint 或 SQLite schema。

## 用户可见变化

- 正式早报/晚报改用 QQ 原生 Markdown。
- 新闻标题直接链接到对应 canonical URL，正文不显示长链接。
- 固定为 Daily News、日期、今日重点、中国、全球政治、金融市场、科技、其他的 Morning Brief 结构。
- 单条新闻保持“标题、2至3句摘要、来源”，不输出 AI 评论或后台指标。
- 超长内容只在板块或完整新闻条目之间切分，不截断标题、摘要或 Markdown 链接。

## Preview

双击 `qq-markdown-preview-test.cmd`。它只读取现有 `news.db`，不会调用 Gemini、Embedding，也不会写 delivery、digest 历史或修改 Event。

Preview 固定带有：`【Daily News V0.4.3 Markdown Preview】`。

## 实际验收

- pytest：45 passed。
- 现有数据库生成 24 个 Event、4 个 Markdown 分段。
- 分段长度：1592、1689、1185、965。
- QQ 原生 Markdown API 实际推送成功。
- Gemini 调用 0，Embedding 生成 0，`news.db` 文件哈希前后一致。
