from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from keio.markdown_model import (
    AttachmentInfo,
    ChecklistItem,
    FooterMetadata,
    ParsedMarkdownNote,
)

FOOTER_RE = re.compile(r"<!--\s*(?:keio|kiko):(\{.*\})\s*-->")
LOCAL_REF_RE = re.compile(r"^(?:!\[[^\]]*]|\[[^\]]+])\(([^)]+)\)$")
CHECKLIST_RE = re.compile(r"^(?P<indent> {0,2})- \[(?P<checked>[ xX])\] (?P<text>.+)$")
URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def extract_footer(text: str) -> tuple[str, FooterMetadata | None]:
    normalized = normalize_newlines(text)
    lines = normalized.splitlines()
    while lines and lines[-1] == "":
        lines.pop()
    if not lines:
        return "", None
    footer_match = FOOTER_RE.fullmatch(lines[-1].strip())
    if footer_match is None:
        return "\n".join(lines), None
    try:
        payload = json.loads(footer_match.group(1))
    except json.JSONDecodeError:
        return "\n".join(lines), None
    footer = FooterMetadata(
        version=int(payload.get("version", 1)),
        keep_name=payload.get("keep_name"),
        keep_update_time=payload.get("keep_update_time"),
        content_sha256=payload.get("content_sha256"),
        synced_at=payload.get("synced_at"),
        title_empty=bool(payload.get("title_empty", False)),
    )
    return "\n".join(lines[:-1]).rstrip("\n"), footer


def format_footer(footer: FooterMetadata) -> str:
    payload = json.dumps(footer.to_dict(), sort_keys=True, separators=(",", ":"))
    return f"<!-- keio:{payload} -->"


def content_sha256(text_without_footer: str) -> str:
    payload = normalize_newlines(text_without_footer).rstrip("\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def parse_markdown_file(path: Path) -> ParsedMarkdownNote:
    text = path.read_text(encoding="utf-8")
    raw_content_without_footer, footer = extract_footer(text)
    lines = raw_content_without_footer.splitlines()

    title = path.stem
    title_from_h1 = False
    cursor = 0
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip()
        title_from_h1 = True
        cursor = 1
        if cursor < len(lines) and lines[cursor] == "":
            cursor += 1

    attachment_dir = path.with_suffix("")
    attachment_files = sorted(
        file_path for file_path in attachment_dir.iterdir() if file_path.is_file()
    ) if attachment_dir.is_dir() else []

    inline_references: list[str] = []
    cursor = _consume_leading_attachment_lines(lines, cursor, inline_references)
    body_lines = lines[cursor:]
    body_markdown = "\n".join(body_lines).strip("\n")

    return ParsedMarkdownNote(
        path=path,
        title=title,
        body_markdown=body_markdown,
        raw_content_without_footer=raw_content_without_footer,
        footer=footer,
        attachments=AttachmentInfo(
            directory=attachment_dir,
            files=attachment_files,
            inline_references=inline_references,
        ),
        title_from_h1=title_from_h1,
    )


def render_markdown_document(
    *,
    title: str,
    title_empty: bool,
    attachment_lines: list[str],
    body_markdown: str,
    footer: FooterMetadata,
) -> str:
    content = render_markdown_content(
        title=title,
        title_empty=title_empty,
        attachment_lines=attachment_lines,
        body_markdown=body_markdown,
    )
    footer_line = format_footer(footer)
    if not content:
        return f"{footer_line}\n"
    return f"{content}\n\n{footer_line}\n"


def render_markdown_content(
    *,
    title: str,
    title_empty: bool,
    attachment_lines: list[str],
    body_markdown: str,
) -> str:
    segments: list[str] = []
    if not title_empty:
        segments.append(f"# {title}")
    if attachment_lines:
        segments.append("\n".join(attachment_lines))
    if body_markdown:
        segments.append(body_markdown.rstrip("\n"))
    return "\n\n".join(segment for segment in segments if segment).rstrip("\n")


def attach_footer_to_content(content_without_footer: str, footer: FooterMetadata) -> str:
    footer_line = format_footer(footer)
    clean_content = normalize_newlines(content_without_footer).rstrip("\n")
    if not clean_content:
        return f"{footer_line}\n"
    return f"{clean_content}\n\n{footer_line}\n"


def parse_checklist_markdown(body_markdown: str) -> list[ChecklistItem] | None:
    lines = body_markdown.splitlines()
    if not lines:
        return []

    items: list[ChecklistItem] = []
    current_parent: ChecklistItem | None = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _line_disqualifies_checklist(stripped):
            return None
        match = CHECKLIST_RE.fullmatch(line)
        if match is None:
            return None
        indent = len(match.group("indent"))
        item = ChecklistItem(
            text=match.group("text").strip(),
            checked=match.group("checked").lower() == "x",
        )
        if indent == 0:
            items.append(item)
            current_parent = item
            continue
        if current_parent is None:
            return None
        current_parent.children.append(item)
    return items


def render_checklist_markdown(items: list[ChecklistItem]) -> str:
    lines: list[str] = []
    for item in items:
        lines.append(_render_checklist_line(item, indent=0))
        for child in item.children:
            lines.append(_render_checklist_line(child, indent=2))
    return "\n".join(lines)


def _consume_leading_attachment_lines(
    lines: list[str],
    cursor: int,
    inline_references: list[str],
) -> int:
    next_cursor = cursor
    while next_cursor < len(lines):
        line = lines[next_cursor].strip()
        if not line:
            if inline_references:
                next_cursor += 1
                break
            return next_cursor + 1
        if not _is_local_reference_line(line):
            break
        inline_references.append(line)
        next_cursor += 1
    return next_cursor


def _is_local_reference_line(line: str) -> bool:
    match = LOCAL_REF_RE.fullmatch(line)
    if match is None:
        return False
    target = match.group(1)
    return URL_SCHEME_RE.match(target) is None


def _line_disqualifies_checklist(stripped_line: str) -> bool:
    return (
        stripped_line.startswith("#")
        or stripped_line.startswith(">")
        or stripped_line.startswith("```")
        or stripped_line.startswith("|")
        or (
            stripped_line.startswith("- ")
            and "[ ]" not in stripped_line
            and "[x]" not in stripped_line.lower()
        )
    )


def _render_checklist_line(item: ChecklistItem, *, indent: int) -> str:
    checked = "x" if item.checked else " "
    return f"{' ' * indent}- [{checked}] {item.text}"
