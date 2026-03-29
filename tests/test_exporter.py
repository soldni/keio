from __future__ import annotations

from pathlib import Path

from kiko.exporter import Exporter
from kiko.markdown_io import parse_markdown_file
from kiko.markdown_model import KeepAttachment, KeepNote


def test_export_text_note_with_footer(tmp_path: Path, fake_keep_client) -> None:
    fake_keep_client._notes["notes/1"] = KeepNote(
        name="notes/1",
        title="Hello",
        update_time="2026-03-29T12:00:00Z",
        kind="text",
        text_body="world",
    )

    summary = Exporter(fake_keep_client).export_directory(tmp_path)

    assert summary.exit_code == 0
    exported = tmp_path / "Hello.md"
    parsed = parse_markdown_file(exported)
    assert parsed.title == "Hello"
    assert parsed.body_markdown == "world"
    assert parsed.footer is not None
    assert parsed.footer.keep_name == "notes/1"


def test_export_downloads_attachments_and_writes_links(tmp_path: Path, fake_keep_client) -> None:
    fake_keep_client._notes["notes/1"] = KeepNote(
        name="notes/1",
        title="Photo",
        update_time="2026-03-29T12:00:00Z",
        kind="text",
        text_body="caption",
        attachments=[KeepAttachment(name="notes/1/attachments/a1", mime_types=["image/png"])],
    )

    summary = Exporter(fake_keep_client).export_directory(tmp_path)

    assert summary.exit_code == 0
    exported = (tmp_path / "Photo.md").read_text(encoding="utf-8")
    assert "![](Photo/image.png)" in exported
    assert (tmp_path / "Photo" / "image.png").exists()


def test_export_skips_modified_tracked_file_without_force(tmp_path: Path, fake_keep_client) -> None:
    note = KeepNote(
        name="notes/1",
        title="Tracked",
        update_time="2026-03-29T12:00:00Z",
        kind="text",
        text_body="server",
    )
    fake_keep_client._notes[note.name] = note
    Exporter(fake_keep_client).export_directory(tmp_path)
    tracked_path = tmp_path / "Tracked.md"
    tracked_path.write_text(
        tracked_path.read_text(encoding="utf-8").replace("server", "local edit"),
        encoding="utf-8",
    )

    summary = Exporter(fake_keep_client).export_directory(tmp_path)

    assert summary.exit_code == 2
    assert "modified" in "\n".join(summary.lines())


def test_export_moves_tracked_file_when_title_changes(tmp_path: Path, fake_keep_client) -> None:
    note = KeepNote(
        name="notes/1",
        title="Old",
        update_time="2026-03-29T12:00:00Z",
        kind="text",
        text_body="body",
    )
    fake_keep_client._notes[note.name] = note
    Exporter(fake_keep_client).export_directory(tmp_path)
    fake_keep_client._notes[note.name] = KeepNote(
        name="notes/1",
        title="New",
        update_time="2026-03-29T12:01:00Z",
        kind="text",
        text_body="body",
    )

    summary = Exporter(fake_keep_client).export_directory(tmp_path, force=True)

    assert summary.exit_code == 0
    assert not (tmp_path / "Old.md").exists()
    assert (tmp_path / "New.md").exists()


def test_export_suffixes_duplicate_titles(tmp_path: Path, fake_keep_client) -> None:
    fake_keep_client._notes["notes/one"] = KeepNote(
        name="notes/one",
        title="Dup",
        update_time="2026-03-29T12:00:00Z",
        kind="text",
        text_body="one",
    )
    fake_keep_client._notes["notes/two"] = KeepNote(
        name="notes/two",
        title="Dup",
        update_time="2026-03-29T12:01:00Z",
        kind="text",
        text_body="two",
    )

    summary = Exporter(fake_keep_client).export_directory(tmp_path)

    assert summary.exit_code == 0
    assert (tmp_path / "Dup [one].md").exists()
    assert (tmp_path / "Dup [two].md").exists()
