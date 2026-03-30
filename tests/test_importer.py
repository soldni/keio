from __future__ import annotations

from pathlib import Path

from keio.importer import Importer, _effective_title
from keio.markdown_io import attach_footer_to_content, content_sha256, parse_markdown_file
from keio.markdown_model import FooterMetadata, KeepNote


def test_import_creates_list_note_and_rewrites_footer(tmp_path: Path, fake_keep_client) -> None:
    note_path = tmp_path / "tasks.md"
    note_path.write_text("# Tasks\n\n- [ ] one\n  - [x] two\n", encoding="utf-8")

    summary = Importer(fake_keep_client).import_directory(tmp_path)

    assert summary.exit_code == 0
    created = fake_keep_client.list_notes()[0]
    assert created.kind == "list"
    assert created.list_items[0].children[0].text == "two"
    assert "keio:" in note_path.read_text(encoding="utf-8")


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


def test_import_tracked_untitled_notes_not_skipped_as_duplicates(
    tmp_path: Path,
    fake_keep_client,
) -> None:
    """Two tracked untitled notes (title_empty=true) must go through the
    replace path, not be skipped as 'duplicate local title'.
    Bug: both produced effective_title="" and the Counter flagged them."""
    for note_id in ("aaa", "bbb"):
        remote = KeepNote(
            name=f"notes/{note_id}",
            title="",
            update_time="2026-03-29T12:00:00Z",
            kind="text",
            text_body=f"body {note_id}",
        )
        fake_keep_client._notes[remote.name] = remote
        local_content = f"body {note_id} edited"
        footer = FooterMetadata(
            keep_name=remote.name,
            keep_update_time="2026-03-29T12:00:00Z",
            content_sha256="stale",
            synced_at="2026-03-29T12:01:00Z",
            title_empty=True,
        )
        path = tmp_path / f"untitled-{note_id}.md"
        path.write_text(attach_footer_to_content(local_content, footer), encoding="utf-8")

    summary = Importer(fake_keep_client).import_directory(tmp_path)

    assert summary.counters.get("skipped", 0) == 0
    assert summary.counters.get("replaced", 0) == 2


def test_import_replaces_changed_text_note(tmp_path: Path, fake_keep_client) -> None:
    """A tracked note whose local content changed and remote timestamp matches
    the footer should be replaced."""
    existing = KeepNote(
        name="notes/tracked",
        title="My Note",
        update_time="2026-03-29T12:00:00Z",
        kind="text",
        text_body="original",
    )
    fake_keep_client._notes[existing.name] = existing
    local_content = "# My Note\n\noriginal"
    footer = FooterMetadata(
        keep_name=existing.name,
        keep_update_time="2026-03-29T12:00:00Z",
        content_sha256=content_sha256(local_content),
        synced_at="2026-03-29T12:01:00Z",
    )
    note_path = tmp_path / "My Note.md"
    note_path.write_text(attach_footer_to_content(local_content, footer), encoding="utf-8")

    # Edit the file locally
    edited_content = "# My Note\n\nedited content"
    note_path.write_text(attach_footer_to_content(edited_content, footer), encoding="utf-8")

    summary = Importer(fake_keep_client).import_directory(tmp_path)

    assert summary.counters.get("replaced", 0) == 1
    new_notes = fake_keep_client.list_notes()
    assert len(new_notes) == 1
    assert new_notes[0].text_body == "edited content"


def test_import_dry_run_does_not_create_notes(tmp_path: Path, fake_keep_client) -> None:
    (tmp_path / "new.md").write_text("# New Note\n\nhello", encoding="utf-8")

    summary = Importer(fake_keep_client).import_directory(tmp_path, dry_run=True)

    assert summary.counters.get("created", 0) == 1
    assert len(fake_keep_client.list_notes()) == 0


def test_import_duplicate_keep_name_is_fatal(tmp_path: Path, fake_keep_client) -> None:
    """Two local files pointing to the same keep_name must be a fatal error."""
    footer = FooterMetadata(
        keep_name="notes/same",
        keep_update_time="2026-03-29T12:00:00Z",
        content_sha256="abc",
    )
    for name in ("a.md", "b.md"):
        (tmp_path / name).write_text(
            attach_footer_to_content(f"# {name}\n\nbody", footer), encoding="utf-8"
        )

    summary = Importer(fake_keep_client).import_directory(tmp_path)

    assert summary.fatal is True
    assert summary.exit_code == 1


def test_effective_title_with_title_empty_flag(tmp_path: Path) -> None:
    """title_empty=true + no H1 -> empty title; H1 always wins."""
    # title_empty with no H1 -> ""
    footer = FooterMetadata(keep_name="notes/1", title_empty=True)
    path = tmp_path / "untitled-abc.md"
    path.write_text(attach_footer_to_content("just body", footer), encoding="utf-8")
    parsed = parse_markdown_file(path)
    assert _effective_title(parsed) == ""

    # title_empty with H1 -> H1 wins
    path2 = tmp_path / "has-h1.md"
    path2.write_text(
        attach_footer_to_content("# Explicit Title\n\nbody", footer), encoding="utf-8"
    )
    parsed2 = parse_markdown_file(path2)
    assert _effective_title(parsed2) == "Explicit Title"

    # No footer -> filename stem
    path3 = tmp_path / "no-footer.md"
    path3.write_text("just body", encoding="utf-8")
    parsed3 = parse_markdown_file(path3)
    assert _effective_title(parsed3) == "no-footer"
