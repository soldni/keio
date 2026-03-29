from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from kiko.auth import AuthStatus
from kiko.cli import app
from kiko.results import OperationSummary

runner = CliRunner()


def test_auth_status_command(monkeypatch) -> None:
    stub_status = lambda: AuthStatus(  # noqa: E731
        logged_in=True,
        token_path=Path("/tmp/token"),
        credentials_path="/tmp/creds",
    )
    monkeypatch.setattr(
        "kiko.cli.status",
        stub_status,
    )

    result = runner.invoke(app, ["auth", "status"])

    assert result.exit_code == 0
    assert "logged_in: yes" in result.stdout


def test_export_command_returns_summary_exit_code(monkeypatch, tmp_path: Path) -> None:
    class StubExporter:
        def __init__(self, client) -> None:
            pass

        def export_directory(
            self,
            directory: Path,
            *,
            dry_run: bool,
            force: bool,
        ) -> OperationSummary:
            summary = OperationSummary()
            summary.increment("exported")
            summary.add_issue("warning", "test warning")
            return summary

    monkeypatch.setattr("kiko.cli._build_client", lambda credentials: object())
    monkeypatch.setattr("kiko.cli.Exporter", StubExporter)

    result = runner.invoke(app, ["export", str(tmp_path)])

    assert result.exit_code == 2
    assert "warning: test warning" in result.stderr
