from __future__ import annotations

from datetime import UTC, datetime


def parse_google_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).astimezone(UTC)


def remote_is_newer(remote_update_time: str | None, footer_update_time: str | None) -> bool:
    remote = parse_google_timestamp(remote_update_time)
    footer = parse_google_timestamp(footer_update_time)
    if remote is None or footer is None:
        return False
    return remote > footer


def remote_matches_footer(remote_update_time: str | None, footer_update_time: str | None) -> bool:
    if not remote_update_time or not footer_update_time:
        return False
    if remote_update_time == footer_update_time:
        return True
    remote = parse_google_timestamp(remote_update_time)
    footer = parse_google_timestamp(footer_update_time)
    if remote is None or footer is None:
        return False
    return remote == footer


def content_hash_matches(current_hash: str, expected_hash: str | None) -> bool:
    return bool(expected_hash) and current_hash == expected_hash
