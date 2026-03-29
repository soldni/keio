from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from urllib.parse import quote

from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from kiko.attachments import choose_preferred_mime_type
from kiko.client_protocol import KeepClientError
from kiko.markdown_model import ChecklistItem, KeepAttachment, KeepNote

__all__ = ["KeepClient", "KeepClientError"]


class KeepClient:
    def __init__(
        self,
        credentials: Credentials,
        *,
        service=None,
        session: AuthorizedSession | None = None,
    ) -> None:
        self._service = service or build(
            "keep",
            "v1",
            credentials=credentials,
            cache_discovery=False,
        )
        self._session = session or AuthorizedSession(credentials)

    def list_notes(self) -> list[KeepNote]:
        notes: list[KeepNote] = []
        page_token: str | None = None
        while True:
            response = self._execute(
                self._service.notes().list(
                    pageSize=100,
                    pageToken=page_token,
                    filter="trashed = false",
                )
            )
            notes.extend(self._convert_note(note) for note in response.get("notes", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return notes

    def get_note(self, name: str) -> KeepNote | None:
        try:
            payload = self._execute(self._service.notes().get(name=name))
        except KeepClientError as error:
            if "404" in str(error):
                return None
            raise
        return self._convert_note(payload)

    def create_text_note(self, title: str, body_markdown: str) -> KeepNote:
        payload: dict[str, object] = {}
        if title:
            payload["title"] = title
        if body_markdown:
            payload["body"] = {"text": {"text": body_markdown}}
        return self._convert_note(self._execute(self._service.notes().create(body=payload)))

    def create_list_note(self, title: str, items: list[ChecklistItem]) -> KeepNote:
        payload: dict[str, object] = {}
        if title:
            payload["title"] = title
        payload["body"] = {"list": {"listItems": self._serialize_list_items(items)}}
        return self._convert_note(self._execute(self._service.notes().create(body=payload)))

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
        try:
            self._execute(self._service.notes().delete(name=name))
        except KeepClientError as error:
            if "404" not in str(error):
                raise

    def download_attachment(self, attachment: KeepAttachment, destination: Path) -> str:
        mime_type = choose_preferred_mime_type(attachment)
        url_name = quote(attachment.name, safe="/")
        response = self._session.get(
            f"https://keep.googleapis.com/v1/{url_name}",
            params={"mimeType": mime_type, "alt": "media"},
            timeout=60,
        )
        try:
            response.raise_for_status()
        except Exception as error:  # pragma: no cover - requests exception shape
            message = f"Failed to download attachment {attachment.name}: {error}"
            raise KeepClientError(message) from error
        destination.write_bytes(response.content)
        return mime_type

    def _execute(self, request) -> dict:
        try:
            return request.execute()
        except HttpError as error:
            raise KeepClientError(str(error)) from error

    def _convert_note(self, payload: dict) -> KeepNote:
        body = payload.get("body", {})
        attachments = [
            KeepAttachment(
                name=attachment.get("name", ""),
                mime_types=list(attachment.get("mimeType", [])),
            )
            for attachment in payload.get("attachments", [])
        ]
        if "list" in body:
            return KeepNote(
                name=payload.get("name", ""),
                title=payload.get("title", ""),
                update_time=payload.get("updateTime"),
                kind="list",
                list_items=self._deserialize_list_items(body.get("list", {}).get("listItems", [])),
                attachments=attachments,
                trashed=bool(payload.get("trashed", False)),
            )
        return KeepNote(
            name=payload.get("name", ""),
            title=payload.get("title", ""),
            update_time=payload.get("updateTime"),
            kind="text",
            text_body=body.get("text", {}).get("text", ""),
            attachments=attachments,
            trashed=bool(payload.get("trashed", False)),
        )

    def _deserialize_list_items(self, items: Iterable[dict]) -> list[ChecklistItem]:
        parsed: list[ChecklistItem] = []
        for item in items:
            parsed.append(
                ChecklistItem(
                    text=item.get("text", {}).get("text", ""),
                    checked=bool(item.get("checked", False)),
                    children=self._deserialize_list_items(item.get("childListItems", [])),
                )
            )
        return parsed

    def _serialize_list_items(self, items: Iterable[ChecklistItem]) -> list[dict[str, object]]:
        payload: list[dict[str, object]] = []
        for item in items:
            encoded: dict[str, object] = {
                "checked": item.checked,
                "text": {"text": item.text},
            }
            if item.children:
                encoded["childListItems"] = self._serialize_list_items(item.children)
            payload.append(encoded)
        return payload
