from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from platformdirs import PlatformDirs

KEEP_SCOPE = "https://www.googleapis.com/auth/keep"


class AuthError(RuntimeError):
    """Authentication setup or token error."""


@dataclass(slots=True, frozen=True)
class AppPaths:
    config_dir: Path
    config_file: Path
    token_file: Path

    @property
    def bundled_credentials_file(self) -> Path:
        return self.config_dir / "credentials.json"


@dataclass(slots=True)
class AuthConfig:
    credentials_path: str | None = None


@dataclass(slots=True)
class AuthStatus:
    logged_in: bool
    token_path: Path
    credentials_path: str | None


@dataclass(slots=True)
class SetupResult:
    stored_credentials_path: Path | None = None
    instructions: list[str] = field(default_factory=list)


def default_paths() -> AppPaths:
    dirs = PlatformDirs(appname="kiko", appauthor=False)
    config_dir = Path(dirs.user_config_dir)
    return AppPaths(
        config_dir=config_dir,
        config_file=config_dir / "config.json",
        token_file=config_dir / "oauth-token.json",
    )


def load_config(*, paths: AppPaths | None = None) -> AuthConfig:
    app_paths = paths or default_paths()
    if not app_paths.config_file.exists():
        return AuthConfig()
    payload = json.loads(app_paths.config_file.read_text(encoding="utf-8"))
    return AuthConfig(credentials_path=payload.get("credentials_path"))


def save_config(config: AuthConfig, *, paths: AppPaths | None = None) -> None:
    app_paths = paths or default_paths()
    app_paths.config_dir.mkdir(parents=True, exist_ok=True)
    app_paths.config_file.write_text(
        json.dumps({"credentials_path": config.credentials_path}, indent=2) + "\n",
        encoding="utf-8",
    )


def login(
    credentials_path: Path | None = None,
    *,
    paths: AppPaths | None = None,
    open_browser: bool = True,
) -> Credentials:
    app_paths = paths or default_paths()
    actual_credentials_path = resolve_credentials_path(
        credentials_path,
        paths=app_paths,
    )
    flow = InstalledAppFlow.from_client_secrets_file(
        str(actual_credentials_path),
        scopes=[KEEP_SCOPE],
    )
    credentials = flow.run_local_server(open_browser=open_browser, port=0)
    _save_credentials(credentials, app_paths.token_file)
    save_config(AuthConfig(credentials_path=str(actual_credentials_path)), paths=app_paths)
    return credentials


def logout(*, paths: AppPaths | None = None) -> bool:
    app_paths = paths or default_paths()
    removed = False
    if app_paths.token_file.exists():
        app_paths.token_file.unlink()
        removed = True
    return removed


def status(*, paths: AppPaths | None = None) -> AuthStatus:
    app_paths = paths or default_paths()
    config = load_config(paths=app_paths)
    return AuthStatus(
        logged_in=app_paths.token_file.exists(),
        token_path=app_paths.token_file,
        credentials_path=config.credentials_path,
    )


def get_credentials(
    *,
    paths: AppPaths | None = None,
    credentials_path: Path | None = None,
    interactive: bool = False,
) -> Credentials:
    app_paths = paths or default_paths()
    token_file = app_paths.token_file
    if token_file.exists():
        credentials = Credentials.from_authorized_user_file(str(token_file), scopes=[KEEP_SCOPE])
        if credentials.valid:
            return credentials
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            _save_credentials(credentials, token_file)
            return credentials
    if not interactive:
        raise AuthError("No valid OAuth token found. Run `kiko auth login` first.")
    return login(credentials_path, paths=app_paths)


def setup(
    *,
    credentials_path: Path | None = None,
    paths: AppPaths | None = None,
) -> SetupResult:
    app_paths = paths or default_paths()
    if credentials_path is not None and not credentials_path.expanduser().exists():
        raise AuthError(f"Credentials file does not exist: {credentials_path.expanduser()}")
    source_credentials = _find_optional_credentials_source(
        credentials_path,
        paths=app_paths,
    )
    if source_credentials is None:
        return SetupResult(instructions=manual_setup_instructions(app_paths))
    return SetupResult(
        stored_credentials_path=install_credentials(
            source_credentials,
            paths=app_paths,
        )
    )


def install_credentials(source: Path, *, paths: AppPaths | None = None) -> Path:
    app_paths = paths or default_paths()
    resolved_source = source.expanduser()
    if not resolved_source.exists():
        raise AuthError(f"Credentials file does not exist: {resolved_source}")
    app_paths.config_dir.mkdir(parents=True, exist_ok=True)
    destination = app_paths.bundled_credentials_file
    if resolved_source.resolve() != destination.resolve():
        shutil.copy2(resolved_source, destination)
    save_config(AuthConfig(credentials_path=str(destination)), paths=app_paths)
    return destination


def resolve_credentials_path(
    credentials_path: Path | None,
    *,
    paths: AppPaths | None = None,
) -> Path:
    app_paths = paths or default_paths()
    candidates = [
        credentials_path,
        _configured_credentials_path(app_paths),
        app_paths.bundled_credentials_file,
        Path.cwd() / "credentials.json",
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        expanded = candidate.expanduser()
        if expanded.exists():
            return expanded
    locations = ", ".join(str(path) for path in candidates if path is not None)
    raise AuthError(
        "No OAuth client credentials file found. "
        "Create a Desktop app OAuth client in the Google Auth platform console, then "
        "pass `--credentials`, or place `credentials.json` in "
        f"{app_paths.bundled_credentials_file} or the current working directory. "
        f"Looked in: {locations}"
    )


def _find_optional_credentials_source(
    credentials_path: Path | None,
    *,
    paths: AppPaths,
) -> Path | None:
    candidates = [
        credentials_path,
        _configured_credentials_path(paths),
        paths.bundled_credentials_file,
        Path.cwd() / "credentials.json",
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        expanded = candidate.expanduser()
        if expanded.exists():
            return expanded
    return None


def _configured_credentials_path(paths: AppPaths) -> Path | None:
    config = load_config(paths=paths)
    if not config.credentials_path:
        return None
    return Path(config.credentials_path)


def _save_credentials(credentials: Credentials, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(credentials.to_json(), encoding="utf-8")


def manual_setup_instructions(paths: AppPaths) -> list[str]:
    return [
        "No OAuth client credentials file is available yet.",
        "Create or download a Desktop app OAuth client:",
        "The Google Keep API is enabled on the project, not on the OAuth client itself.",
        "1. Go to https://cloud.google.com/.",
        "2. Choose your Google Cloud project, or create one.",
        '3. Search for "Google Keep API", open it, and click Enable for this project.',
        (
            '4. Search for "Google Auth platform" in the top search bar and open it.'
        ),
        (
            "5. If no app exists yet, use the setup wizard to create one "
            "(app info, audience, and contact details)."
        ),
        "6. Open Clients.",
        (
            "7. Create a Desktop app client, or open the existing "
            "Desktop app client and download JSON."
        ),
        (
            f"8. Save the downloaded file as `{paths.bundled_credentials_file}` "
            "or rerun `uv run kiko auth setup --credentials /path/to/credentials.json`."
        ),
        "9. Then run `uv run kiko auth login`.",
        "Reference: https://developers.google.com/workspace/guides/create-credentials",
        "Reference: https://developers.google.com/workspace/guides/enable-apis",
    ]
