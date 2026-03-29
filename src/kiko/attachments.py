from __future__ import annotations

import mimetypes
from pathlib import Path

from kiko.markdown_model import KeepAttachment

IMAGE_MIME_PREFIX = "image/"
IMAGE_MIME_PREFERENCE = [
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/heic",
    "image/tiff",
    "image/webp",
    "image/gif",
]


def choose_preferred_mime_type(attachment: KeepAttachment) -> str:
    if not attachment.mime_types:
        return "application/octet-stream"
    for preferred in IMAGE_MIME_PREFERENCE:
        if preferred in attachment.mime_types:
            return preferred
    return attachment.mime_types[0]


def extension_for_mime_type(mime_type: str) -> str:
    extension = mimetypes.guess_extension(mime_type, strict=False)
    if extension == ".jpe":
        return ".jpg"
    if extension:
        return extension
    if mime_type == "image/heic":
        return ".heic"
    return ".bin"


def attachment_filename(kind: str, index: int, mime_type: str) -> str:
    base = kind if index == 1 else f"{kind}_{index}"
    return f"{base}{extension_for_mime_type(mime_type)}"


def is_image_attachment(attachment: KeepAttachment) -> bool:
    return any(mime.startswith(IMAGE_MIME_PREFIX) for mime in attachment.mime_types)


def markdown_reference(stem: str, filename: str, is_image: bool) -> str:
    relative = Path(stem) / filename
    if is_image:
        return f"![]({relative.as_posix()})"
    return f"[{filename}]({relative.as_posix()})"
