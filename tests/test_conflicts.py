from __future__ import annotations

from kiko.conflicts import remote_matches_footer


def test_remote_matches_footer_exact() -> None:
    assert remote_matches_footer("2026-03-29T12:00:00Z", "2026-03-29T12:00:00Z") is True


def test_remote_matches_footer_fractional_seconds() -> None:
    assert remote_matches_footer("2026-03-29T12:00:00.000Z", "2026-03-29T12:00:00Z") is True


def test_remote_matches_footer_different_times() -> None:
    assert remote_matches_footer("2026-03-29T12:00:01Z", "2026-03-29T12:00:00Z") is False


def test_remote_matches_footer_none() -> None:
    assert remote_matches_footer(None, "2026-03-29T12:00:00Z") is False
    assert remote_matches_footer("2026-03-29T12:00:00Z", None) is False
