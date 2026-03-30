from __future__ import annotations

from pathlib import Path

from kiko.client_protocol import KeepClientError
from kiko.markdown_model import ChecklistItem, KeepAttachment, KeepNote


class GkeepApiClient:
    """KeepClientProtocol implementation backed by gkeepapi."""

    def __init__(self, keep: object) -> None:
        self._keep = keep
        self._blob_cache: dict[str, object] = {}

    def list_notes(self) -> list[KeepNote]:
        notes: list[KeepNote] = []
        for node in self._keep.all():  # type: ignore[union-attr]
            if node.trashed:
                continue
            notes.append(self._convert_note(node))
        return notes

    def get_note(self, name: str) -> KeepNote | None:
        node = self._keep.get(name)  # type: ignore[union-attr]
        if node is None:
            return None
        return self._convert_note(node)

    def create_text_note(self, title: str, body_markdown: str) -> KeepNote:
        node = self._keep.createNote(title or None, body_markdown or None)  # type: ignore[union-attr]
        self._keep.sync()  # type: ignore[union-attr]
        return self._convert_note(node)

    def create_list_note(self, title: str, items: list[ChecklistItem]) -> KeepNote:
        flat_items = [(item.text, item.checked) for item in items]
        node = self._keep.createList(title or None, flat_items)  # type: ignore[union-attr]

        # Add child items via indent() after creation
        if any(item.children for item in items):
            list_items = list(node.items)
            for flat_idx, source_item in enumerate(items):
                if flat_idx >= len(list_items):
                    break
                parent = list_items[flat_idx]
                for child in source_item.children:
                    child_node = node.add(child.text, child.checked)
                    child_node.indent(parent)

        self._keep.sync()  # type: ignore[union-attr]
        return self._convert_note(node)

    def replace_text_note(
        self, existing_name: str, title: str, body_markdown: str
    ) -> KeepNote:
        new_note = self.create_text_note(title, body_markdown)
        self.delete_note(existing_name)
        return new_note

    def replace_list_note(
        self, existing_name: str, title: str, items: list[ChecklistItem]
    ) -> KeepNote:
        new_note = self.create_list_note(title, items)
        self.delete_note(existing_name)
        return new_note

    def delete_note(self, name: str) -> None:
        node = self._keep.get(name)  # type: ignore[union-attr]
        if node is None:
            return
        node.trash()
        self._keep.sync()  # type: ignore[union-attr]

    def sync(self) -> None:
        self._keep.sync()  # type: ignore[union-attr]

    def download_attachment(self, attachment: KeepAttachment, destination: Path) -> str:
        blob = self._blob_cache.get(attachment.name)
        if blob is None:
            raise KeepClientError(
                f"Attachment download not available for {attachment.name} "
                "(gkeepapi blob reference not found)"
            )
        url = getattr(blob, "url", None)
        if not url:
            raise KeepClientError(
                f"No download URL for attachment {attachment.name} via gkeepapi"
            )
        import urllib.request

        try:
            urllib.request.urlretrieve(url, str(destination))
        except Exception as exc:
            raise KeepClientError(
                f"Failed to download attachment {attachment.name}: {exc}"
            ) from exc
        mime_type = (
            attachment.mime_types[0] if attachment.mime_types else "application/octet-stream"
        )
        return mime_type

    # ------------------------------------------------------------------
    # Conversion helpers
    # ------------------------------------------------------------------

    def _convert_note(self, node: object) -> KeepNote:
        import gkeepapi.node as gnode

        is_list = isinstance(node, gnode.List)
        note_id = node.id  # type: ignore[union-attr]
        title = node.title or ""  # type: ignore[union-attr]

        update_time: str | None = None
        try:
            ts = node.timestamps.updated  # type: ignore[union-attr]
            if ts:
                update_time = ts.isoformat().replace("+00:00", "Z")
        except (AttributeError, TypeError):
            pass

        attachments = self._convert_blobs(node)

        if is_list:
            return KeepNote(
                name=note_id,
                title=title,
                update_time=update_time,
                kind="list",
                list_items=self._convert_list_items(node),  # type: ignore[arg-type]
                attachments=attachments,
                trashed=bool(node.trashed),  # type: ignore[union-attr]
            )
        return KeepNote(
            name=note_id,
            title=title,
            update_time=update_time,
            kind="text",
            text_body=node.text or "",  # type: ignore[union-attr]
            attachments=attachments,
            trashed=bool(node.trashed),  # type: ignore[union-attr]
        )

    def _convert_list_items(self, node: object) -> list[ChecklistItem]:
        items: list[ChecklistItem] = []
        for li in node.items:  # type: ignore[union-attr]
            children = [
                ChecklistItem(
                    text=sub.text or "",
                    checked=bool(sub.checked),
                )
                for sub in (li.subitems if hasattr(li, "subitems") else [])
            ]
            items.append(
                ChecklistItem(
                    text=li.text or "",
                    checked=bool(li.checked),
                    children=children,
                )
            )
        return items

    def _convert_blobs(self, node: object) -> list[KeepAttachment]:
        attachments: list[KeepAttachment] = []
        blobs = getattr(node, "blobs", None) or []
        note_id = getattr(node, "id", "unknown")
        for idx, blob in enumerate(blobs):
            att_name = f"{note_id}/blob/{idx}"
            mime = getattr(blob, "mimetype", None) or "application/octet-stream"
            attachments.append(KeepAttachment(name=att_name, mime_types=[mime]))
            self._blob_cache[att_name] = blob
        return attachments
