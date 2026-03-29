from __future__ import annotations

import json
from dataclasses import dataclass
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


@dataclass(slots=True)
class AuthConfig:
    credentials_path: str | None = None


@dataclass(slots=True)
class AuthStatus:
    logged_in: bool
    token_path: Path
    credentials_path: str | None


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
    credentials_path: Path,
    *,
    paths: AppPaths | None = None,
    open_browser: bool = True,
) -> Credentials:
    app_paths = paths or default_paths()
    if not credentials_path.exists():
        raise AuthError(f"Credentials file does not exist: {credentials_path}")
    flow = InstalledAppFlow.from_client_secrets_file(
        str(credentials_path),
        scopes=[KEEP_SCOPE],
    )
    credentials = flow.run_local_server(open_browser=open_browser, port=0)
    _save_credentials(credentials, app_paths.token_file)
    save_config(AuthConfig(credentials_path=str(credentials_path)), paths=app_paths)
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
    actual_credentials_path = credentials_path or _credentials_path_from_config(app_paths)
    if actual_credentials_path is None:
        raise AuthError("No credentials path provided. Use `--credentials` or run auth login.")
    return login(actual_credentials_path, paths=app_paths)


def _credentials_path_from_config(paths: AppPaths) -> Path | None:
    config = load_config(paths=paths)
    if not config.credentials_path:
        return None
    return Path(config.credentials_path)


def _save_credentials(credentials: Credentials, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(credentials.to_json(), encoding="utf-8")
