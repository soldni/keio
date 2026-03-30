from __future__ import annotations

from pathlib import Path

import typer

from kiko import __version__
from kiko.auth import (
    AuthError,
    AuthMethod,
    build_keep_client,
    login,
    logout,
    setup,
    status,
)
from kiko.exporter import Exporter
from kiko.importer import Importer
from kiko.results import OperationSummary

app = typer.Typer(no_args_is_help=True)
auth_app = typer.Typer(no_args_is_help=True)
app.add_typer(auth_app, name="auth")

SETUP_CREDENTIALS_OPTION = typer.Option(None, "--credentials")
DRY_RUN_OPTION = typer.Option(False, "--dry-run")
FORCE_OPTION = typer.Option(False, "--force")
CREDENTIALS_OPTION = typer.Option(None, "--credentials")
METHOD_OPTION = typer.Option(None, "--method", help="enterprise or gkeepapi")
IMAGES_OPTION = typer.Option(
    False,
    "--images",
    help="Open browser and file explorer for manual image upload per note.",
)


def main() -> None:
    app()


@app.callback()
def root_callback() -> None:
    """kiko Google Keep CLI."""


@app.command("version")
def version() -> None:
    typer.echo(__version__)


@auth_app.command("login")
def auth_login() -> None:
    """Run the auth flow for the configured method and cache a token."""
    try:
        login()
    except AuthError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo("Logged in.")


@auth_app.command("setup")
def auth_setup(
    method: str | None = METHOD_OPTION,
    credentials: str | None = typer.Option(None, "--credentials"),
) -> None:
    """Store OAuth client credentials or gkeepapi master token."""
    auth_method: AuthMethod | None = None
    if method is not None:
        try:
            auth_method = AuthMethod(method)
        except ValueError:
            typer.echo(
                f"error: unknown method '{method}'. Use 'enterprise' or 'gkeepapi'.",
                err=True,
            )
            raise typer.Exit(code=1) from None

    # --credentials can be a JSON string or a file path
    credentials_path: Path | None = None
    credentials_json: str | None = None
    if credentials is not None:
        stripped = credentials.strip()
        if stripped.startswith("{"):
            credentials_json = stripped
        else:
            credentials_path = Path(credentials)

    try:
        result = setup(
            method=auth_method,
            credentials_path=credentials_path,
            credentials_json=credentials_json,
        )
    except AuthError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error

    if result.stored_credentials_path is not None:
        typer.echo(f"credentials_path: {result.stored_credentials_path}")
        typer.echo("next: kiko auth login")
        return

    for line in result.instructions:
        typer.echo(line, err=True)
    raise typer.Exit(code=2)


@auth_app.command("logout")
def auth_logout() -> None:
    """Remove the cached OAuth token or gkeepapi state."""
    removed = logout()
    typer.echo("Logged out." if removed else "No cached token found.")


@auth_app.command("status")
def auth_status() -> None:
    """Show auth cache status."""
    current = status()
    typer.echo(f"method: {current.method or '(unset)'}")
    typer.echo(f"logged_in: {'yes' if current.logged_in else 'no'}")
    typer.echo(f"token_path: {current.token_path}")
    typer.echo(f"credentials_path: {current.credentials_path or '(unset)'}")


@app.command("export")
def export_notes(
    directory: Path,
    dry_run: bool = DRY_RUN_OPTION,
    force: bool = FORCE_OPTION,
    credentials: Path | None = CREDENTIALS_OPTION,
) -> None:
    """Export Keep notes into a markdown directory."""
    _run_operation(
        lambda: Exporter(_build_client(credentials), log=_log).export_directory(
            directory,
            dry_run=dry_run,
            force=force,
        )
    )


@app.command("import")
def import_notes(
    directory: Path,
    dry_run: bool = DRY_RUN_OPTION,
    force: bool = FORCE_OPTION,
    credentials: Path | None = CREDENTIALS_OPTION,
    images: bool = IMAGES_OPTION,
) -> None:
    """Import markdown notes into Google Keep."""
    _run_operation(
        lambda: Importer(_build_client(credentials), log=_log).import_directory(
            directory,
            dry_run=dry_run,
            force=force,
            images=images,
        )
    )


def _log(msg: str) -> None:
    typer.echo(msg, err=True)


def _build_client(credentials_path: Path | None) -> object:
    try:
        return build_keep_client(
            credentials_path=credentials_path,
            interactive=credentials_path is not None,
            log=_log,
        )
    except AuthError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error


def _run_operation(factory) -> None:
    try:
        summary: OperationSummary = factory()
    except typer.Exit:
        raise
    except Exception as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error

    for line in summary.lines():
        typer.echo(line, err=line.startswith(("error:", "warning:", "skip:")))
    raise typer.Exit(code=summary.exit_code)
