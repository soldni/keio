from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from keio.auth import AuthStatus, SetupResult
from keio.cli import app
from keio.results import OperationSummary

runner = CliRunner()


def test_auth_status_command(monkeypatch) -> None:
    stub_status = lambda: AuthStatus(  # noqa: E731
        logged_in=True,
        token_path=Path("/tmp/token"),
        credentials_path="/tmp/creds",
        method="enterprise",
    )
    monkeypatch.setattr("keio.cli.status", stub_status)

    result = runner.invoke(app, ["auth", "status"])

    assert result.exit_code == 0
    assert "logged_in: yes" in result.stdout
    assert "method: enterprise" in result.stdout


def test_export_command_returns_summary_exit_code(monkeypatch, tmp_path: Path) -> None:
    class StubExporter:
        def __init__(self, client, **_kwargs) -> None:
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

    monkeypatch.setattr("keio.cli._build_client", lambda credentials: object())
    monkeypatch.setattr("keio.cli.Exporter", StubExporter)

    result = runner.invoke(app, ["export", str(tmp_path)])

    assert result.exit_code == 2
    assert "warning: test warning" in result.stderr


def test_auth_setup_enterprise_prints_stored_credentials(monkeypatch) -> None:
    monkeypatch.setattr(
        "keio.cli.setup",
        lambda method, credentials_path, credentials_json: SetupResult(
            stored_credentials_path=Path("/tmp/credentials.json"),
        ),
    )

    result = runner.invoke(app, ["auth", "setup", "--method", "enterprise"])

    assert result.exit_code == 0
    assert "credentials_path: /tmp/credentials.json" in result.stdout


def test_auth_setup_prints_manual_steps(monkeypatch) -> None:
    monkeypatch.setattr(
        "keio.cli.setup",
        lambda method, credentials_path, credentials_json: SetupResult(
            instructions=[
                "The official Google Keep API requires a Google Workspace Enterprise subscription.",
                "Create or download a Desktop app OAuth client.",
            ],
        ),
    )

    result = runner.invoke(app, ["auth", "setup", "--method", "enterprise"])

    assert result.exit_code == 2
    assert "Enterprise" in result.stderr


def test_auth_setup_gkeepapi_prints_instructions(monkeypatch) -> None:
    monkeypatch.setattr(
        "keio.cli.setup",
        lambda method, credentials_path, credentials_json: SetupResult(
            instructions=["gkeepapi uses the unofficial mobile Google Keep API."],
        ),
    )

    result = runner.invoke(app, ["auth", "setup", "--method", "gkeepapi"])

    assert result.exit_code == 2
    assert "gkeepapi" in result.stderr


def test_auth_setup_invalid_method() -> None:
    result = runner.invoke(app, ["auth", "setup", "--method", "invalid"])
    assert result.exit_code == 1
    assert "unknown method" in result.stderr


def test_auth_status_shows_method(monkeypatch) -> None:
    monkeypatch.setattr(
        "keio.cli.status",
        lambda: AuthStatus(
            logged_in=False,
            token_path=Path("/tmp/t"),
            credentials_path=None,
            method="gkeepapi",
        ),
    )

    result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 0
    assert "method: gkeepapi" in result.stdout
