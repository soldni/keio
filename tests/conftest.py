from __future__ import annotations

from dataclasses import replace

import pytest

from keio.markdown_model import ChecklistItem, KeepAttachment, KeepNote


class FakeKeepClient:
    def __init__(self, notes: list[KeepNote] | None = None) -> None:
        self._notes: dict[str, KeepNote] = {note.name: note for note in notes or []}
        self._counter = len(self._notes)
        self.downloads: list[tuple[str, str]] = []

    def list_notes(self) -> list[KeepNote]:
        return list(self._notes.values())

    def get_note(self, name: str) -> KeepNote | None:
        return self._notes.get(name)

    def create_text_note(self, title: str, body_markdown: str) -> KeepNote:
        note = KeepNote(
            name=self._new_name(),
            title=title,
            update_time=self._new_timestamp(),
            kind="text",
            text_body=body_markdown,
        )
        self._notes[note.name] = note
        return note

    def create_list_note(self, title: str, items: list[ChecklistItem]) -> KeepNote:
        note = KeepNote(
            name=self._new_name(),
            title=title,
            update_time=self._new_timestamp(),
            kind="list",
            list_items=items,
        )
        self._notes[note.name] = note
        return note

    def replace_text_note(self, existing_name: str, title: str, body_markdown: str) -> KeepNote:
        new_note = self.create_text_note(title, body_markdown)
        self.delete_note(existing_name)
        return new_note

    def replace_list_note(
        self,
        existing_name: str,
        title: str,
        items: list[ChecklistItem],
    ) -> KeepNote:
        new_note = self.create_list_note(title, items)
        self.delete_note(existing_name)
        return new_note

    def delete_note(self, name: str) -> None:
        self._notes.pop(name, None)

    def sync(self) -> None:
        pass

    def download_attachment(self, attachment: KeepAttachment, destination) -> str:
        mime_type = attachment.mime_types[0]
        destination.write_bytes(f"attachment:{attachment.name}".encode())
        self.downloads.append((attachment.name, destination.name))
        return mime_type

    def add_attachment(self, note_name: str, attachment: KeepAttachment) -> KeepNote:
        note = self._notes[note_name]
        updated = replace(note, attachments=[*note.attachments, attachment])
        self._notes[note_name] = updated
        return updated

    def _new_name(self) -> str:
        self._counter += 1
        return f"notes/n{self._counter:04d}"

    def _new_timestamp(self) -> str:
        return f"2026-03-29T12:{self._counter:02d}:00Z"


@pytest.fixture
def fake_keep_client() -> FakeKeepClient:
    return FakeKeepClient()
