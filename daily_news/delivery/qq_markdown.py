from __future__ import annotations

import re
from datetime import date


H2_RE = re.compile(r"^##\s+.+$", re.MULTILINE)
H3_RE = re.compile(r"^###\s+(.+)$", re.MULTILINE)
TITLE_LINK_RE = re.compile(r"^###\s+(?:[①②③④⑤]\s+)?\[[^\]]+\]\(https?://[^)\s]+\)\s*$")
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(https?://[^)\s]+\)")
BARE_URL_RE = re.compile(r"https?://\S+")

SECTION_ORDER = ("中国", "全球政治", "金融市场", "科技", "其他")
TOP_NUMBERS = ("①", "②", "③", "④", "⑤")


def _escape_title(value: str) -> str:
    return value.replace("[", "［").replace("]", "］").replace("\n", " ").strip()


def _primary_url(event: dict) -> str:
    articles = [item for item in event.get("articles", []) if item.get("canonical_url")]
    return articles[-1]["canonical_url"] if articles else ""


def _sources(event: dict) -> str:
    names = []
    for article in event.get("articles", []):
        name = str(article.get("source_name") or "").strip()
        if name and name not in names:
            names.append(name)
    return "｜".join(names[:4]) or "Daily News"


def _event_item(event: dict, number: str = "") -> str:
    title = _escape_title(str(event.get("canonical_title") or "未命名事件"))
    url = _primary_url(event)
    if not url:
        raise ValueError(f"Event {event.get('id', '<unknown>')} 没有 canonical_url，无法生成可点击标题")
    summary = str(event.get("summary") or "").strip()
    update = str(event.get("update_label") or "").strip() if event.get("is_update") else ""
    prefix = f"{number} " if number else ""
    lines = [f"### {prefix}[{title}]({url})", ""]
    if update:
        lines.extend([f"**更新：{update}**", ""])
    if summary:
        lines.extend([summary, ""])
    lines.append(f"来源：{_sources(event)}")
    return "\n".join(lines)


def build_event_markdown_digest(
    events: list[dict],
    edition_date: date,
    preview_label: str | None = None,
    top_count: int = 5,
) -> str:
    """Build a deterministic, link-safe digest from already ranked Events."""
    if not events:
        raise ValueError("没有可生成简报的 Event")
    top_count = min(max(top_count, 3), 5, len(events))
    top, remaining = events[:top_count], events[top_count:]
    lines = ["# Daily News", "", edition_date.isoformat(), ""]
    if preview_label:
        lines.extend([f"> {preview_label}", ""])
    lines.extend(["## 今日重点", ""])
    for index, event in enumerate(top):
        lines.extend([_event_item(event, TOP_NUMBERS[index]), ""])

    for section in SECTION_ORDER:
        section_events = [event for event in remaining if event.get("digest_section") == section]
        if not section_events:
            continue
        lines.extend(["---", "", f"## {section}", ""])
        for event in section_events:
            lines.extend([_event_item(event), ""])
    return "\n".join(lines).strip()


def validate_news_markdown(markdown: str) -> None:
    text = (markdown or "").replace("\r\n", "\n").strip()
    if not text.startswith("# Daily News"):
        raise ValueError("Markdown 简报必须以 '# Daily News' 开头")
    headings = H3_RE.findall(text)
    if not headings:
        raise ValueError("Markdown 简报没有新闻条目")
    for line in text.splitlines():
        if line.startswith("### ") and not TITLE_LINK_RE.fullmatch(line.strip()):
            raise ValueError(f"新闻标题不是完整 Markdown 超链接：{line}")
        without_links = MARKDOWN_LINK_RE.sub("", line)
        if BARE_URL_RE.search(without_links):
            raise ValueError("Markdown 简报正文中存在裸 URL")


def _split_h2_sections(markdown: str) -> tuple[str, list[str]]:
    matches = list(H2_RE.finditer(markdown))
    if not matches:
        return markdown.strip(), []
    preamble = markdown[:matches[0].start()].strip()
    sections = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        sections.append(markdown[match.start():end].strip().strip("-\n "))
    return preamble, sections


def _split_large_section(
    section: str,
    max_chars: int,
    first_preamble: str,
    continuation_preamble: str,
) -> list[str]:
    lines = section.splitlines()
    heading = lines[0].strip()
    body = "\n".join(lines[1:]).strip()
    item_matches = list(re.finditer(r"(?m)^###\s+", body))
    if not item_matches:
        raise ValueError(f"板块超过 QQ 长度限制且没有可安全切分的新闻条目：{heading}")
    lead = body[:item_matches[0].start()].strip()
    items = []
    for index, match in enumerate(item_matches):
        end = item_matches[index + 1].start() if index + 1 < len(item_matches) else len(body)
        items.append(body[match.start():end].strip())

    chunks: list[str] = []
    current = f"{first_preamble}\n\n{heading}" if first_preamble else heading
    if lead:
        current += f"\n\n{lead}"
    for item in items:
        candidate = f"{current}\n\n{item}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        empty_headers = {
            first_preamble.strip(), continuation_preamble.strip(), heading,
            f"{first_preamble}\n\n{heading}".strip(),
            f"{continuation_preamble}\n\n{heading}".strip(),
        }
        if current.strip() not in empty_headers:
            chunks.append(current)
            current = f"{continuation_preamble}\n\n{heading}"
        candidate = f"{current}\n\n{item}"
        if len(candidate) > max_chars:
            raise ValueError(f"单条新闻超过 QQ 长度限制，拒绝从标题、摘要或超链接中间截断：{item[:80]}")
        current = candidate
    if current:
        chunks.append(current)
    return chunks


def split_qq_markdown(markdown: str, max_chars: int = 1800) -> list[str]:
    """Split only between H2 sections or complete H3 news items."""
    text = (markdown or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    validate_news_markdown(text)
    if len(text) <= max_chars:
        return [text]

    preamble, sections = _split_h2_sections(text)
    if not sections:
        raise ValueError("Markdown 简报超长且没有板块标题，无法安全分段")
    continuation = "# Daily News（续）"
    messages: list[str] = []
    current = preamble
    for section in sections:
        candidate = f"{current}\n\n{section}" if current else section
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current and current not in {preamble, continuation}:
            messages.append(current)
            current = continuation

        candidate = f"{current}\n\n{section}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        first_preamble = current or continuation
        pieces = _split_large_section(section, max_chars, first_preamble, continuation)
        messages.extend(pieces[:-1])
        current = pieces[-1]
    if current:
        messages.append(current)
    if any(len(message) > max_chars for message in messages):
        raise ValueError("Markdown 分段后仍超过 QQ 长度限制")
    return messages
