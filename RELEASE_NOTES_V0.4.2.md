# Daily News V0.4.2 Official Sources

本版直接基于用户上传且已验证的 V0.4.1，仅完善 Bloomberg 与 Trump X 来源接入研究；没有改动 Gemini 模型、Embedding 模型、持久化 checkpoint、聚类/重要性目标、Breaking 入口、pending 口径、Reuters、White House 或 QQ Bot。

## BloombergLatestAdapter

- 新增 `daily_news/collectors/bloomberg_latest.py`。
- 主入口固定为 Bloomberg 官方公开的 `https://www.bloomberg.com/sitemaps/news/latest.xml`。
- 该入口由 Bloomberg 自己的 `robots.txt` 以 `Sitemap:` 明确公布，不是作者 RSS、第三方镜像或猜测 URL。
- `fetch_method=bloomberg_latest_sitemap`，`source_id/source_group=bloomberg`，`official=false`（媒体来源，不是政府声明源）。
- 只读取标题、发布时间、最后修改时间和 canonical article URL；不获取文章正文、Cookie、登录内容或付费墙内容。
- 默认每轮取最新 100 条；SQLite canonical URL/content hash 精确去重使后续每小时运行只处理新增条目。
- 任何 403、429、timeout、SSL、5xx 或 XML 结构错误均由现有来源隔离机制记录，不影响其他来源。

## 真实 Bloomberg 网络验证

- `https://www.bloomberg.com/latest`：普通 HTTP + 正常浏览器 headers 返回 403；云浏览器显示 Bloomberg “Are you a robot?” 页面，没有尝试或绕过验证。
- `https://www.bloomberg.com/robots.txt`：HTTP 200，明确列出 `sitemaps/news/latest.xml`。
- `https://www.bloomberg.com/sitemaps/news/latest.xml`：HTTP 200；本次完整 XML 有 466 条，覆盖约两天滚动新闻。
- V0.4.2 adapter 真实运行成功返回限制后的 100 篇 Article；首条具备 title、UTC `published_at` 和 canonical URL。
- 不需要 Cookie、API Key 或登录，不触碰 paywall；适合每小时无人值守 discovery，但 sitemap 不等于有 SLA 的正式 API，结构变化时会安全失败。

## Trump X 无 Token 专项结论

- X 官方公开 profile 在本次浏览器/HTTP 实测中无需登录即可显示有限帖子及 post URL。
- X Publish/oEmbed 能返回 embedded timeline 的 HTML 代码，但不直接返回 `post_id/text/published_at` 数据。
- timeline 内容依赖浏览器组件；公开 syndication 请求在实测中返回 429，profile HTML 中的数据属于未文档化的内部 React 流，不能作为稳定后端契约。
- 因此没有新增 `TrumpXPublicAdapter`。现有 X API v2 adapter 原样保留；需要 `X_BEARER_TOKEN`，无 Token 时优雅关闭。
- 继续保留 `social_primary`、`primary_source=true`、`source_group=donald_trump` 与“特朗普在 X 表示”的 attribution 语义。

## 测试

新增覆盖：Bloomberg sitemap Article 映射、二次抓取不重复 Embedding、Reuters 跨源聚类、403 来源隔离、canonical URL、tracking 参数精确去重。全部 V0.4/V0.4.1 测试继续保留。

## 数据库与密钥

- 不需要新的 SQLite migration。
- 发布包不包含 `.env`、`news.db`、`.venv`、output、Cookie 或任何真实 Gemini/QQ/X secret。
