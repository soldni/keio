from __future__ import annotations

from pathlib import Path

from keio.markdown_io import (
    attach_footer_to_content,
    content_sha256,
    extract_footer,
    format_footer,
    parse_checklist_markdown,
    parse_markdown_file,
    render_checklist_markdown,
    render_markdown_content,
)
from keio.markdown_model import FooterMetadata


def test_extract_footer_roundtrip() -> None:
    footer = FooterMetadata(
        keep_name="notes/123",
        keep_update_time="2026-03-29T12:00:00Z",
        content_sha256="abc",
        synced_at="2026-03-29T12:01:00Z",
    )
    document = "hello\n\n" + format_footer(footer) + "\n"
    content, parsed_footer = extract_footer(document)
    assert content == "hello"
    assert parsed_footer is not None
    assert parsed_footer.keep_name == "notes/123"


def test_parse_markdown_file_strips_leading_attachment_block(tmp_path: Path) -> None:
    note_path = tmp_path / "note.md"
    (tmp_path / "note").mkdir()
    (tmp_path / "note" / "image.png").write_bytes(b"png")
    note_path.write_text("# Note\n\n![](legacy/path.png)\n\nbody\n", encoding="utf-8")

    parsed = parse_markdown_file(note_path)

    assert parsed.title == "Note"
    assert parsed.body_markdown == "body"
    assert parsed.attachments.has_files


def test_parse_checklist_markdown_accepts_one_level_children() -> None:
    body = "- [ ] parent\n  - [x] child"
    items = parse_checklist_markdown(body)
    assert items is not None
    assert items[0].text == "parent"
    assert items[0].children[0].checked is True
    assert render_checklist_markdown(items) == body


def test_extract_footer_ignores_malformed_json() -> None:
    content, footer = extract_footer("hello\n\n<!-- keio:{bad json} -->")
    # Malformed footer line is preserved as content, not stripped
    assert content == "hello\n\n<!-- keio:{bad json} -->"
    assert footer is None


def test_content_sha256_uses_footerless_content() -> None:
    content = "# Title\n\nbody"
    footer = FooterMetadata(keep_name="notes/abc")
    document = attach_footer_to_content(content, footer)
    body, _ = extract_footer(document)
    assert content_sha256(body) == content_sha256(content)


def test_parse_checklist_empty_body() -> None:
    """Empty body returns an empty list (valid but empty checklist)."""
    assert parse_checklist_markdown("") == []


def test_parse_checklist_disqualified_by_heading() -> None:
    assert parse_checklist_markdown("# Heading\n- [ ] item") is None


def test_parse_checklist_disqualified_by_plain_list() -> None:
    assert parse_checklist_markdown("- [ ] check\n- plain item") is None


def test_parse_checklist_child_without_parent_returns_none() -> None:
    """A child item appearing before any parent is invalid."""
    assert parse_checklist_markdown("  - [ ] orphan child") is None


def test_render_markdown_content_untitled() -> None:
    """title_empty=True omits the H1 heading."""
    result = render_markdown_content(
        title="",
        title_empty=True,
        attachment_lines=[],
        body_markdown="just body",
    )
    assert result == "just body"
    assert not result.startswith("#")


def test_render_markdown_content_with_attachments_and_body() -> None:
    result = render_markdown_content(
        title="Title",
        title_empty=False,
        attachment_lines=["![](img/photo.png)"],
        body_markdown="paragraph",
    )
    assert result == "# Title\n\n![](img/photo.png)\n\nparagraph"


def test_extract_footer_empty_file() -> None:
    content, footer = extract_footer("")
    assert content == ""
    assert footer is None


def test_extract_footer_preserves_kiko_backward_compat() -> None:
    """Footers with the old 'kiko:' prefix must still parse."""
    old_footer = '<!-- kiko:{"version":1,"keep_name":"notes/old"} -->'
    content, footer = extract_footer(f"hello\n\n{old_footer}")
    assert content == "hello"
    assert footer is not None
    assert footer.keep_name == "notes/old"


def test_content_sha256_normalizes_line_endings() -> None:
    """CRLF and CR are normalized to LF before hashing."""
    assert content_sha256("a\r\nb") == content_sha256("a\nb")
    assert content_sha256("a\rb") == content_sha256("a\nb")
