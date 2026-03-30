from __future__ import annotations

import platform
import select
import subprocess
import sys
import time
import webbrowser
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from kiko.client_protocol import KeepClientProtocol
from kiko.conflicts import content_hash_matches, remote_is_newer, remote_matches_footer
from kiko.markdown_io import (
    attach_footer_to_content,
    content_sha256,
    parse_checklist_markdown,
    parse_markdown_file,
)
from kiko.markdown_model import FooterMetadata, ParsedMarkdownNote
from kiko.results import OperationSummary

POLL_INTERVAL_SECONDS = 5
POLL_TIMEOUT_SECONDS = 300


def _noop(_msg: str) -> None:
    pass


class Importer:
    def __init__(
        self,
        client: KeepClientProtocol,
        *,
        log: Callable[[str], None] = _noop,
    ) -> None:
        self._client = client
        self._log = log

    def import_directory(
        self,
        directory: Path,
        *,
        dry_run: bool = False,
        force: bool = False,
        images: bool = False,
    ) -> OperationSummary:
        summary = OperationSummary()
        if not directory.exists():
            summary.fatal = True
            summary.add_issue("error", f"Directory does not exist: {directory}")
            return summary

        self._log("Scanning local markdown files...")
        md_paths = sorted(directory.glob("*.md"))
        parsed_notes = [parse_markdown_file(path) for path in md_paths]
        self._log(f"Found {len(parsed_notes)} local file(s).")

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

        self._log("Fetching remote notes...")
        remote_notes = self._client.list_notes()
        self._log(f"Found {len(remote_notes)} remote note(s).")
        notes_by_title: dict[str, list[str]] = {}
        for note in remote_notes:
            notes_by_title.setdefault(note.title, []).append(note.name)

        total = len(parsed_notes)
        for idx, note in enumerate(parsed_notes, 1):
            label = _display_title(note)
            effective_title = _effective_title(note)
            if duplicates.get(effective_title, 0) > 1:
                self._log(f"[{idx}/{total}] Skipped {label} (duplicate local title)")
                summary.increment("skipped")
                summary.add_issue(
                    "skip",
                    f"Skipped duplicate local title `{effective_title}` in {note.path.name}",
                )
                continue
            has_images = note.attachments.has_files
            if has_images:
                if images:
                    summary.add_issue(
                        "warning",
                        f"{note.path.name} has local attachments; "
                        "will assist with manual upload.",
                    )
                else:
                    summary.add_issue(
                        "warning",
                        f"{note.path.name} has local attachments; "
                        "importing note content only.",
                    )

            checklist_items = parse_checklist_markdown(note.body_markdown)
            is_list = checklist_items is not None
            kind_tag = "list" if is_list else "text"
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
                local_hash = content_sha256(note.raw_content_without_footer)
                content_changed = not content_hash_matches(
                    local_hash, footer.content_sha256
                )

                if not force and not footer_matches:
                    if remote_is_newer(remote_note.update_time, footer.keep_update_time):
                        self._log(f"[{idx}/{total}] Skipped {label} (remote is newer)")
                        summary.increment("skipped")
                        summary.add_issue(
                            "skip",
                            f"Remote note is newer than local file: {note.path.name}",
                        )
                    else:
                        self._log(f"[{idx}/{total}] Skipped {label} (timestamp mismatch)")
                        summary.increment("skipped")
                        summary.add_issue(
                            "skip",
                            f"Remote note timestamp mismatch for {note.path.name}",
                        )
                    if images and has_images:
                        self._assist_image_upload(remote_note.name, note)
                    continue

                if not force and not content_changed:
                    if images and has_images:
                        self._log(f"[{idx}/{total}] Unchanged {label} (images pending)")
                        summary.increment("unchanged")
                        self._assist_image_upload(remote_note.name, note)
                    else:
                        self._log(f"[{idx}/{total}] Unchanged {label}")
                        summary.increment("unchanged")
                    continue

                if dry_run:
                    self._log(f"[{idx}/{total}] Would replace {label} ({kind_tag})")
                    summary.increment("replaced")
                    continue
                self._log(f"[{idx}/{total}] Replacing {label} ({kind_tag})")
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
                if images and has_images and not dry_run:
                    self._assist_image_upload(new_note.name, note)
                continue

            if effective_title in notes_by_title:
                self._log(f"[{idx}/{total}] Skipped {label} (title exists in Keep)")
                summary.increment("skipped")
                summary.add_issue(
                    "skip",
                    f"Keep already has a note titled `{effective_title}`; "
                    f"skipped {note.path.name}",
                )
                continue

            if dry_run:
                self._log(f"[{idx}/{total}] Would create {label} ({kind_tag})")
                summary.increment("created")
                continue

            self._log(f"[{idx}/{total}] Creating {label} ({kind_tag})")
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
            if images and has_images:
                self._assist_image_upload(created_note.name, note)

        return summary

    # ------------------------------------------------------------------
    # Image upload assist
    # ------------------------------------------------------------------

    def _assist_image_upload(
        self,
        keep_name: str,
        parsed: ParsedMarkdownNote,
    ) -> None:
        n_files = len(parsed.attachments.files)
        url = _keep_note_url(keep_name)
        self._log(f"  {n_files} file(s) to upload from {parsed.attachments.directory.name}/")
        self._log(f"  Opening note in browser: {url}")
        webbrowser.open(url)
        _open_file_explorer(parsed.attachments.directory)

        self._log("  Polling for uploads... (press Enter to skip)")
        uploaded = _wait_for_attachments(
            self._client,
            keep_name,
            n_files,
            self._log,
        )
        if uploaded:
            found = n_files if uploaded >= n_files else uploaded
            summary_label = f"{found}/{n_files}"
            self._log(f"  {summary_label} attachment(s) detected. Continuing.")
        else:
            self._log("  Skipped image upload.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Image upload polling
# ---------------------------------------------------------------------------


def _wait_for_attachments(
    client: KeepClientProtocol,
    note_name: str,
    expected: int,
    log: Callable[[str], None],
) -> int:
    """Poll until the note has >= expected attachments. Returns count found, or 0 if skipped."""
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    interactive = sys.stdin.isatty()

    while time.monotonic() < deadline:
        if interactive and _stdin_ready():
            sys.stdin.readline()
            return 0

        time.sleep(POLL_INTERVAL_SECONDS)

        try:
            client.sync()
            note = client.get_note(note_name)
        except Exception:
            continue

        if note is None:
            continue

        count = len(note.attachments)
        if count >= expected:
            return count
        if count > 0:
            log(f"  {count}/{expected} uploaded...")

    log(f"  Timed out after {POLL_TIMEOUT_SECONDS}s.")
    return 0


def _stdin_ready() -> bool:
    """Check if stdin has input available without blocking."""
    if sys.platform == "win32":
        try:
            import msvcrt

            return msvcrt.kbhit()
        except ImportError:
            return False
    try:
        return bool(select.select([sys.stdin], [], [], 0)[0])
    except (ValueError, OSError):
        return False


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------


def _keep_note_url(note_name: str) -> str:
    note_id = note_name.rsplit("/", maxsplit=1)[-1]
    return f"https://keep.google.com/#NOTE/{note_id}"


def _open_file_explorer(path: Path) -> None:
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", str(path)])
        elif system == "Windows":
            subprocess.Popen(["explorer", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


def _display_title(note: ParsedMarkdownNote) -> str:
    title = _effective_title(note)
    return f'"{title}"' if title else f"({note.path.name})"


def _effective_title(note: ParsedMarkdownNote) -> str:
    if note.footer and note.footer.title_empty and not note.title_from_h1:
        return ""
    return note.title


def _utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
