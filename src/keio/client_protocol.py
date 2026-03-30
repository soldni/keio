from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from keio.markdown_model import ChecklistItem, KeepAttachment, KeepNote


class KeepClientError(RuntimeError):
    """Keep API failure."""


@runtime_checkable
class KeepClientProtocol(Protocol):
    def list_notes(self) -> list[KeepNote]: ...

    def get_note(self, name: str) -> KeepNote | None: ...

    def create_text_note(self, title: str, body_markdown: str) -> KeepNote: ...

    def create_list_note(self, title: str, items: list[ChecklistItem]) -> KeepNote: ...

    def replace_text_note(
        self, existing_name: str, title: str, body_markdown: str
    ) -> KeepNote: ...

    def replace_list_note(
        self, existing_name: str, title: str, items: list[ChecklistItem]
    ) -> KeepNote: ...

    def delete_note(self, name: str) -> None: ...

    def download_attachment(self, attachment: KeepAttachment, destination: Path) -> str: ...

    def sync(self) -> None: ...
