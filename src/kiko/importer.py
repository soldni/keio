from __future__ import annotations

from collections import Counter
from pathlib import Path

from kiko.conflicts import remote_is_newer, remote_matches_footer
from kiko.keep_client import KeepClient
from kiko.markdown_io import (
    attach_footer_to_content,
    content_sha256,
    parse_checklist_markdown,
    parse_markdown_file,
)
from kiko.markdown_model import FooterMetadata, ParsedMarkdownNote
from kiko.results import OperationSummary


class Importer:
    def __init__(self, client: KeepClient) -> None:
        self._client = client

    def import_directory(
        self,
        directory: Path,
        *,
        dry_run: bool = False,
        force: bool = False,
    ) -> OperationSummary:
        summary = OperationSummary()
        if not directory.exists():
            summary.fatal = True
            summary.add_issue("error", f"Directory does not exist: {directory}")
            return summary

        parsed_notes = [parse_markdown_file(path) for path in sorted(directory.glob("*.md"))]
        duplicates = self._duplicate_titles(parsed_notes)
        duplicate_keep_names = self._duplicate_keep_names(parsed_notes)
        if duplicate_keep_names:
            summary.fatal = True
            for keep_name in duplicate_keep_names:
                summary.add_issue(
                    "error",
                    f"Duplicate footer keep_name in import batch: {keep_name}",
                )
            return summary

        remote_notes = self._client.list_notes()
        notes_by_title: dict[str, list[str]] = {}
        for note in remote_notes:
            notes_by_title.setdefault(note.title, []).append(note.name)

        for note in parsed_notes:
            effective_title = _effective_title(note)
            if duplicates.get(effective_title, 0) > 1:
                summary.increment("skipped")
                summary.add_issue(
                    "skip",
                    f"Skipped duplicate local title `{effective_title}` in {note.path.name}",
                )
                continue
            if note.attachments.has_files:
                summary.add_issue(
                    "warning",
                    f"{note.path.name} has local attachments; importing note content only.",
                )

            checklist_items = parse_checklist_markdown(note.body_markdown)
            is_list = checklist_items is not None
            footer = note.footer
            remote_note = (
                self._client.get_note(footer.keep_name)
                if footer and footer.keep_name
                else None
            )

            if footer and footer.keep_name and remote_note is not None:
                footer_matches = remote_matches_footer(
                    remote_note.update_time,
                    footer.keep_update_time,
                )
                if not force and not footer_matches:
                    if remote_is_newer(remote_note.update_time, footer.keep_update_time):
                        summary.increment("skipped")
                        summary.add_issue(
                            "skip",
                            f"Remote note is newer than local file: {note.path.name}",
                        )
                    else:
                        summary.increment("skipped")
                        summary.add_issue(
                            "skip",
                            f"Remote note timestamp mismatch for {note.path.name}",
                        )
                    continue
                if dry_run:
                    summary.increment("replaced")
                    continue
                new_note = (
                    self._client.replace_list_note(
                        remote_note.name,
                        effective_title,
                        checklist_items or [],
                    )
                    if is_list
                    else self._client.replace_text_note(
                        remote_note.name,
                        effective_title,
                        note.body_markdown,
                    )
                )
                self._rewrite_footer(
                    note,
                    new_note.name,
                    new_note.update_time,
                    effective_title == "",
                )
                summary.increment("replaced")
                continue

            if effective_title in notes_by_title:
                summary.increment("skipped")
                summary.add_issue(
                    "skip",
                    f"Keep already has a note titled `{effective_title}`; skipped {note.path.name}",
                )
                continue

            if dry_run:
                summary.increment("created")
                continue

            created_note = (
                self._client.create_list_note(effective_title, checklist_items or [])
                if is_list
                else self._client.create_text_note(effective_title, note.body_markdown)
            )
            self._rewrite_footer(
                note,
                created_note.name,
                created_note.update_time,
                effective_title == "",
            )
            notes_by_title.setdefault(effective_title, []).append(created_note.name)
            summary.increment("created")

        return summary

    def _duplicate_titles(self, notes: list[ParsedMarkdownNote]) -> Counter[str]:
        return Counter(_effective_title(note) for note in notes)

    def _duplicate_keep_names(self, notes: list[ParsedMarkdownNote]) -> set[str]:
        keep_names = [
            note.footer.keep_name
            for note in notes
            if note.footer is not None and note.footer.keep_name is not None
        ]
        return {name for name, count in Counter(keep_names).items() if count > 1}

    def _rewrite_footer(
        self,
        note: ParsedMarkdownNote,
        keep_name: str,
        keep_update_time: str | None,
        title_empty: bool,
    ) -> None:
        footer = FooterMetadata(
            version=1,
            keep_name=keep_name,
            keep_update_time=keep_update_time,
            content_sha256=content_sha256(note.raw_content_without_footer),
            synced_at=_utc_now(),
            title_empty=title_empty,
        )
        note.path.write_text(
            attach_footer_to_content(note.raw_content_without_footer, footer),
            encoding="utf-8",
        )


def _effective_title(note: ParsedMarkdownNote) -> str:
    if note.footer and note.footer.title_empty and not note.title_from_h1:
        return ""
    return note.title


def _utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
