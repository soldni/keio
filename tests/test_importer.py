from __future__ import annotations

from pathlib import Path

from kiko.importer import Importer
from kiko.markdown_io import attach_footer_to_content
from kiko.markdown_model import FooterMetadata, KeepNote


def test_import_creates_list_note_and_rewrites_footer(tmp_path: Path, fake_keep_client) -> None:
    note_path = tmp_path / "tasks.md"
    note_path.write_text("# Tasks\n\n- [ ] one\n  - [x] two\n", encoding="utf-8")

    summary = Importer(fake_keep_client).import_directory(tmp_path)

    assert summary.exit_code == 0
    created = fake_keep_client.list_notes()[0]
    assert created.kind == "list"
    assert created.list_items[0].children[0].text == "two"
    assert "kiko:" in note_path.read_text(encoding="utf-8")


def test_import_warns_for_attachments_and_imports_body_only(
    tmp_path: Path,
    fake_keep_client,
) -> None:
    attachment_dir = tmp_path / "note"
    attachment_dir.mkdir()
    (attachment_dir / "image.png").write_bytes(b"png")
    note_path = tmp_path / "note.md"
    note_path.write_text("# Note\n\n![](Bear/path.png)\n\nbody text\n", encoding="utf-8")

    summary = Importer(fake_keep_client).import_directory(tmp_path)

    assert summary.exit_code == 2
    created = fake_keep_client.list_notes()[0]
    assert created.text_body == "body text"


def test_import_skips_when_remote_note_is_newer(tmp_path: Path, fake_keep_client) -> None:
    existing = KeepNote(
        name="notes/existing",
        title="Tracked",
        update_time="2026-03-29T12:10:00Z",
        kind="text",
        text_body="remote",
    )
    fake_keep_client._notes[existing.name] = existing
    local_content = "# Tracked\n\nlocal"
    footer = FooterMetadata(
        keep_name=existing.name,
        keep_update_time="2026-03-29T12:00:00Z",
        content_sha256="abc",
        synced_at="2026-03-29T12:01:00Z",
    )
    note_path = tmp_path / "tracked.md"
    note_path.write_text(attach_footer_to_content(local_content, footer), encoding="utf-8")

    summary = Importer(fake_keep_client).import_directory(tmp_path)

    assert summary.exit_code == 2
    assert len(fake_keep_client.list_notes()) == 1
    assert "Remote note is newer" in "\n".join(summary.lines())


def test_import_skips_same_title_without_footer(tmp_path: Path, fake_keep_client) -> None:
    fake_keep_client._notes["notes/existing"] = KeepNote(
        name="notes/existing",
        title="Collision",
        update_time="2026-03-29T12:00:00Z",
        kind="text",
        text_body="remote",
    )
    (tmp_path / "collision.md").write_text("# Collision\n\nlocal", encoding="utf-8")

    summary = Importer(fake_keep_client).import_directory(tmp_path)

    assert summary.exit_code == 2
    assert len(fake_keep_client.list_notes()) == 1
