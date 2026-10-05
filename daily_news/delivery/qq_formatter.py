from __future__ import annotations

import re


HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
PLAIN_HEADING_RE = re.compile(r"^【[^\n】]+】$")
NUMBERED_ITEM_RE = re.compile(r"^\s*\d+[.、]\s+")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
BARE_LINK_RE = re.compile(r"https?://\S+")


def markdown_to_qq_plain(markdown: str) -> str:
    """Convert editor Markdown into mobile-friendly QQ plain text."""
    text = (markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    output: list[str] = []
    in_code_block = False

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if line.startswith("```"):
            in_code_block = not in_code_block
            continue
        if not line:
            output.append("")
            continue
        if re.fullmatch(r"[-*_]{3,}", line):
            continue

        heading = HEADING_RE.match(line)
        if heading:
            title = _strip_inline_markdown(heading.group(1))
            output.extend(([""] if output and output[-1] else []) + [f"【{title}】", ""])
            continue

        line = re.sub(r"^>\s?", "", line)
        line = re.sub(r"^[-*+]\s+", "• ", line)
        line = MARKDOWN_LINK_RE.sub(lambda match: f"{match.group(1)}\n{match.group(2)}", line)
        line = _strip_inline_markdown(line)
        output.append(line)

    normalized: list[str] = []
    for line in output:
        if not line and (not normalized or not normalized[-1]):
            continue
        normalized.append(line)
    while normalized and not normalized[-1]:
        normalized.pop()
    return "\n".join(normalized).strip()


def _strip_inline_markdown(value: str) -> str:
    value = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", value)
    value = value.replace("**", "").replace("__", "")
    value = re.sub(r"(?<!\w)[*_](.+?)[*_](?!\w)", r"\1", value)
    value = value.replace("`", "")
    return value.strip()


def _sections(text: str) -> list[tuple[str, str]]:
    """Return (heading, body) sections. Empty heading represents preamble."""
    sections: list[tuple[str, list[str]]] = []
    current_heading = ""
    current_body: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if PLAIN_HEADING_RE.fullmatch(stripped):
            if current_heading or any(item.strip() for item in current_body):
                sections.append((current_heading, current_body))
            current_heading, current_body = stripped, []
        else:
            current_body.append(line.rstrip())
    if current_heading or any(item.strip() for item in current_body):
        sections.append((current_heading, current_body))
    return [(heading, "\n".join(body).strip()) for heading, body in sections]


def _item_units(body: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]
    if not paragraphs:
        return []
    units: list[str] = []
    current: list[str] = []
    for paragraph in paragraphs:
        if NUMBERED_ITEM_RE.match(paragraph) and current:
            units.append("\n\n".join(current))
            current = [paragraph]
        else:
            current.append(paragraph)
    if current:
        units.append("\n\n".join(current))
    return units


def _split_oversized_unit(unit: str, limit: int) -> list[str]:
    paragraphs = [part.strip() for part in unit.split("\n") if part.strip()]
    pieces: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= limit:
            pieces.append(paragraph)
            continue
        sentences = [part.strip() for part in re.split(r"(?<=[。！？.!?])\s*", paragraph) if part.strip()]
        current = ""
        for sentence in sentences:
            candidate = sentence if not current else current + sentence
            if len(candidate) <= limit:
                current = candidate
                continue
            if current:
                pieces.append(current)
            if len(sentence) <= limit or BARE_LINK_RE.fullmatch(sentence):
                current = sentence
            else:
                # Last-resort safety for pathological unbroken text; normal news items never reach this path.
                pieces.extend(sentence[index:index + limit] for index in range(0, len(sentence), limit))
                current = ""
        if current:
            pieces.append(current)
    return pieces


def _split_large_section(heading: str, body: str, max_chars: int) -> list[str]:
    continuation = heading[:-1] + "（续）】" if heading else "【续】"
    header = heading or ""
    units = _item_units(body) or [body]
    chunks: list[str] = []
    current = header

    for unit in units:
        separator = "\n\n" if current else ""
        if len(current) + len(separator) + len(unit) <= max_chars:
            current += separator + unit
            continue
        if current and current != header:
            chunks.append(current)
            current = continuation
        elif current == header and not body:
            chunks.append(current)
            current = continuation

        available = max_chars - len(current) - (2 if current else 0)
        if len(unit) <= available:
            current += ("\n\n" if current else "") + unit
            continue
        for piece in _split_oversized_unit(unit, max(200, available)):
            separator = "\n\n" if current else ""
            if len(current) + len(separator) + len(piece) > max_chars and current:
                chunks.append(current)
                current = continuation
                separator = "\n\n"
            current += separator + piece
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk.strip()]


def split_qq_plain_text(text: str, max_chars: int = 1800) -> list[str]:
    """Split on sections first, then whole news items, with sentence fallback."""
    clean = text.strip()
    if not clean:
        return []
    if len(clean) <= max_chars:
        return [clean]

    section_texts: list[str] = []
    for heading, body in _sections(clean):
        section = heading + (("\n\n" + body) if heading and body else body)
        if len(section) <= max_chars:
            section_texts.append(section)
        else:
            section_texts.extend(_split_large_section(heading, body, max_chars))

    messages: list[str] = []
    current = ""
    for section in section_texts:
        separator = "\n\n" if current else ""
        if len(current) + len(separator) + len(section) <= max_chars:
            current += separator + section
        else:
            if current:
                messages.append(current)
            current = section
    if current:
        messages.append(current)
    return messages


def qq_messages_from_markdown(markdown: str, max_chars: int = 1800) -> list[str]:
    return split_qq_plain_text(markdown_to_qq_plain(markdown), max_chars=max_chars)
