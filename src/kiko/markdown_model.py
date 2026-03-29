from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

NoteKind = Literal["text", "list"]


@dataclass(slots=True)
class FooterMetadata:
    version: int = 1
    keep_name: str | None = None
    keep_update_time: str | None = None
    content_sha256: str | None = None
    synced_at: str | None = None
    title_empty: bool = False

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"version": self.version}
        if self.keep_name:
            payload["keep_name"] = self.keep_name
        if self.keep_update_time:
            payload["keep_update_time"] = self.keep_update_time
        if self.content_sha256:
            payload["content_sha256"] = self.content_sha256
        if self.synced_at:
            payload["synced_at"] = self.synced_at
        if self.title_empty:
            payload["title_empty"] = True
        return payload


@dataclass(slots=True)
class AttachmentInfo:
    directory: Path
    files: list[Path] = field(default_factory=list)
    inline_references: list[str] = field(default_factory=list)

    @property
    def has_files(self) -> bool:
        return bool(self.files)


@dataclass(slots=True)
class ChecklistItem:
    text: str
    checked: bool
    children: list[ChecklistItem] = field(default_factory=list)


@dataclass(slots=True)
class ParsedMarkdownNote:
    path: Path
    title: str
    body_markdown: str
    raw_content_without_footer: str
    footer: FooterMetadata | None
    attachments: AttachmentInfo
    title_from_h1: bool


@dataclass(slots=True)
class KeepAttachment:
    name: str
    mime_types: list[str]


@dataclass(slots=True)
class KeepNote:
    name: str
    title: str
    update_time: str | None
    kind: NoteKind
    text_body: str = ""
    list_items: list[ChecklistItem] = field(default_factory=list)
    attachments: list[KeepAttachment] = field(default_factory=list)
    trashed: bool = False
