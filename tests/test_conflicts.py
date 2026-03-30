from __future__ import annotations

from keio.conflicts import content_hash_matches, remote_is_newer, remote_matches_footer


def test_remote_matches_footer_exact() -> None:
    assert remote_matches_footer("2026-03-29T12:00:00Z", "2026-03-29T12:00:00Z") is True


def test_remote_matches_footer_fractional_seconds() -> None:
    assert remote_matches_footer("2026-03-29T12:00:00.000Z", "2026-03-29T12:00:00Z") is True


def test_remote_matches_footer_different_times() -> None:
    assert remote_matches_footer("2026-03-29T12:00:01Z", "2026-03-29T12:00:00Z") is False


def test_remote_matches_footer_none() -> None:
    assert remote_matches_footer(None, "2026-03-29T12:00:00Z") is False
    assert remote_matches_footer("2026-03-29T12:00:00Z", None) is False


def test_content_hash_matches_equal() -> None:
    assert content_hash_matches("abc123", "abc123") is True


def test_content_hash_matches_different() -> None:
    assert content_hash_matches("abc123", "def456") is False


def test_content_hash_matches_none_expected() -> None:
    """None or empty expected hash always returns False (treat as 'changed')."""
    assert content_hash_matches("abc123", None) is False
    assert content_hash_matches("abc123", "") is False


def test_remote_is_newer_true() -> None:
    assert remote_is_newer("2026-03-29T12:10:00Z", "2026-03-29T12:00:00Z") is True


def test_remote_is_newer_false_when_equal() -> None:
    assert remote_is_newer("2026-03-29T12:00:00Z", "2026-03-29T12:00:00Z") is False


def test_remote_is_newer_false_when_older() -> None:
    assert remote_is_newer("2026-03-29T11:00:00Z", "2026-03-29T12:00:00Z") is False


def test_remote_is_newer_false_when_none() -> None:
    assert remote_is_newer(None, "2026-03-29T12:00:00Z") is False
    assert remote_is_newer("2026-03-29T12:00:00Z", None) is False
