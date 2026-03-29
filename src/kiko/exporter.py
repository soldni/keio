from __future__ import annotations

import shutil
import tempfile
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from kiko.attachments import (
    attachment_filename,
    choose_preferred_mime_type,
    is_image_attachment,
    markdown_reference,
)
from kiko.client_protocol import KeepClientError, KeepClientProtocol
from kiko.conflicts import content_hash_matches
from kiko.markdown_io import (
    content_sha256,
    parse_markdown_file,
    render_checklist_markdown,
    render_markdown_content,
    render_markdown_document,
)
from kiko.markdown_model import FooterMetadata, KeepAttachment, KeepNote, ParsedMarkdownNote
from kiko.results import OperationSummary

INVALID_FILENAME_CHARS = '<>:"/\\|?*'

def _noop(_msg: str) -> None:
    pass


class Exporter:
    def __init__(
        self,
        client: KeepClientProtocol,
        *,
        log: Callable[[str], None] = _noop,
    ) -> None:
        self._client = client
        self._log = log

    def export_directory(
        self,
        directory: Path,
        *,
        dry_run: bool = False,
        force: bool = False,
    ) -> OperationSummary:
        summary = OperationSummary()
        if not directory.exists() and not dry_run:
            directory.mkdir(parents=True, exist_ok=True)
        elif not directory.exists():
            summary.increment("planned_directory_creations")

        self._log("Indexing local directory...")
        local_index = self._build_local_index(directory, summary)
        if summary.fatal:
            return summary

        self._log("Fetching remote notes...")
        remote_notes = self._client.list_notes()
        self._log(f"Found {len(remote_notes)} remote note(s).")
        stems = [self._preferred_stem(note) for note in remote_notes]
        collisions = Counter(stems)

        total = len(remote_notes)
        for idx, note in enumerate(remote_notes, 1):
            label = f'"{note.title}"' if note.title else f"(untitled {_short_id(note.name)})"
            preferred_stem = self._preferred_stem(note)
            if collisions[preferred_stem] > 1:
                preferred_stem = f"{preferred_stem} [{_short_id(note.name)}]"
            destination = directory / f"{preferred_stem}.md"
            tracked = local_index.get(note.name)
            if tracked and not force:
                current_hash = content_sha256(tracked.raw_content_without_footer)
                expected_hash = tracked.footer.content_sha256 if tracked.footer else None
                if not content_hash_matches(current_hash, expected_hash):
                    self._log(f"[{idx}/{total}] Skipped {label} (local edit)")
                    summary.increment("skipped")
                    summary.add_issue(
                        "skip",
                        f"Tracked local file modified; skipped export for {note.name}",
                    )
                    continue

            attachment_lines: list[str]
            markdown_body = (
                note.text_body
                if note.kind == "text"
                else render_checklist_markdown(note.list_items)
            )
            try:
                attachment_lines, attachment_dir = self._prepare_attachments(
                    directory=directory,
                    stem=preferred_stem,
                    attachments=note.attachments,
                )
            except KeepClientError as error:
                self._log(f"[{idx}/{total}] Skipped {label} (attachment error)")
                summary.increment("skipped")
                summary.add_issue("skip", f"Attachment download failed for {note.name}: {error}")
                continue

            title_empty = note.title == ""
            content_without_footer = render_markdown_content(
                title=note.title,
                title_empty=title_empty,
                attachment_lines=attachment_lines,
                body_markdown=markdown_body,
            )
            footer = FooterMetadata(
                version=1,
                keep_name=note.name,
                keep_update_time=note.update_time,
                content_sha256=content_sha256(content_without_footer),
                synced_at=_utc_now(),
                title_empty=title_empty,
            )
            document = render_markdown_document(
                title=note.title,
                title_empty=title_empty,
                attachment_lines=attachment_lines,
                body_markdown=markdown_body,
                footer=footer,
            )

            if dry_run:
                self._log(f"[{idx}/{total}] Would export {label}")
                summary.increment("exported")
                if attachment_dir is not None:
                    shutil.rmtree(attachment_dir)
                continue

            self._log(f"[{idx}/{total}] Exporting {label}")
            self._write_note(
                destination=destination,
                document=document,
                attachment_dir=attachment_dir,
                tracked=tracked,
            )
            summary.increment("exported")

        return summary

    def _build_local_index(
        self,
        directory: Path,
        summary: OperationSummary,
    ) -> dict[str, ParsedMarkdownNote]:
        index: dict[str, ParsedMarkdownNote] = {}
        for path in sorted(directory.glob("*.md")):
            parsed = parse_markdown_file(path)
            keep_name = parsed.footer.keep_name if parsed.footer else None
            if not keep_name:
                continue
            if keep_name in index:
                summary.fatal = True
                summary.add_issue(
                    "error",
                    f"Duplicate local footer keep_name during export: {keep_name}",
                )
                return {}
            index[keep_name] = parsed
        return index

    def _prepare_attachments(
        self,
        *,
        directory: Path,
        stem: str,
        attachments: list[KeepAttachment],
    ) -> tuple[list[str], Path | None]:
        if not attachments:
            return [], None

        temp_dir = Path(tempfile.mkdtemp(prefix=f".kiko-{stem}-", dir=directory))
        image_index = 0
        attachment_index = 0
        markdown_lines: list[str] = []

        try:
            for attachment in attachments:
                mime_type = choose_preferred_mime_type(attachment)
                if is_image_attachment(attachment):
                    image_index += 1
                    filename = attachment_filename("image", image_index, mime_type)
                    is_image = True
                else:
                    attachment_index += 1
                    filename = attachment_filename("attachment", attachment_index, mime_type)
                    is_image = False
                destination = temp_dir / filename
                self._client.download_attachment(attachment, destination)
                markdown_lines.append(markdown_reference(stem, filename, is_image))
            return markdown_lines, temp_dir
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    def _preferred_stem(self, note: KeepNote) -> str:
        if note.title:
            return _sanitize_stem(note.title)
        return f"untitled-{_short_id(note.name)}"

    def _write_note(
        self,
        *,
        destination: Path,
        document: str,
        attachment_dir: Path | None,
        tracked: ParsedMarkdownNote | None,
    ) -> None:
        old_markdown_path = tracked.path if tracked else None
        old_attachment_dir = tracked.path.with_suffix("") if tracked else None

        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=".kiko-",
            suffix=".md",
            delete=False,
        ) as handle:
            handle.write(document)
            temp_markdown = Path(handle.name)

        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_markdown.replace(destination)

        destination_attachment_dir = destination.with_suffix("")
        if destination_attachment_dir.exists():
            shutil.rmtree(destination_attachment_dir)
        if attachment_dir is None:
            if (
                old_attachment_dir
                and old_attachment_dir.exists()
                and old_attachment_dir != destination_attachment_dir
            ):
                shutil.rmtree(old_attachment_dir)
        else:
            attachment_dir.replace(destination_attachment_dir)

        if old_markdown_path and old_markdown_path != destination and old_markdown_path.exists():
            old_markdown_path.unlink()
        if (
            old_attachment_dir
            and old_attachment_dir != destination_attachment_dir
            and old_attachment_dir.exists()
        ):
            shutil.rmtree(old_attachment_dir)


def _sanitize_stem(value: str) -> str:
    sanitized = "".join(
        "_" if char in INVALID_FILENAME_CHARS or ord(char) < 32 else char
        for char in value
    )
    sanitized = sanitized.strip().rstrip(".")
    return sanitized or "untitled"


def _short_id(name: str) -> str:
    tail = name.rsplit("/", maxsplit=1)[-1]
    return tail[:8]


def _utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
