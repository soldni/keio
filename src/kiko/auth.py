from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from platformdirs import PlatformDirs


def _noop(_msg: str) -> None:
    pass

KEEP_SCOPE = "https://www.googleapis.com/auth/keep"


class AuthError(RuntimeError):
    """Authentication setup or token error."""


class AuthMethod(Enum):
    ENTERPRISE = "enterprise"
    GKEEPAPI = "gkeepapi"


@dataclass(slots=True, frozen=True)
class AppPaths:
    config_dir: Path
    config_file: Path
    token_file: Path

    @property
    def bundled_credentials_file(self) -> Path:
        return self.config_dir / "credentials.json"

    @property
    def master_token_file(self) -> Path:
        return self.config_dir / "master-token.json"

    @property
    def gkeepapi_state_file(self) -> Path:
        return self.config_dir / "gkeepapi-state.json"


@dataclass(slots=True)
class AuthConfig:
    method: str | None = None
    credentials_path: str | None = None


@dataclass(slots=True)
class AuthStatus:
    logged_in: bool
    token_path: Path
    credentials_path: str | None
    method: str | None = None


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
    return AuthConfig(
        method=payload.get("method"),
        credentials_path=payload.get("credentials_path"),
    )


def save_config(config: AuthConfig, *, paths: AppPaths | None = None) -> None:
    app_paths = paths or default_paths()
    app_paths.config_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {}
    if config.method is not None:
        payload["method"] = config.method
    if config.credentials_path is not None:
        payload["credentials_path"] = config.credentials_path
    app_paths.config_file.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def login(
    credentials_path: Path | None = None,
    *,
    paths: AppPaths | None = None,
    open_browser: bool = True,
) -> object:
    """Run the auth flow for the configured method and cache tokens."""
    app_paths = paths or default_paths()
    config = load_config(paths=app_paths)
    method = _resolve_method(config, app_paths)

    if method == AuthMethod.GKEEPAPI:
        return _gkeepapi_login(paths=app_paths)
    return _enterprise_login(
        credentials_path,
        paths=app_paths,
        open_browser=open_browser,
    )


def _enterprise_login(
    credentials_path: Path | None = None,
    *,
    paths: AppPaths,
    open_browser: bool = True,
) -> object:
    from google_auth_oauthlib.flow import InstalledAppFlow

    actual_credentials_path = resolve_credentials_path(credentials_path, paths=paths)
    flow = InstalledAppFlow.from_client_secrets_file(
        str(actual_credentials_path),
        scopes=[KEEP_SCOPE],
    )
    credentials = flow.run_local_server(open_browser=open_browser, port=0)
    _save_enterprise_credentials(credentials, paths.token_file)
    save_config(
        AuthConfig(
            method=AuthMethod.ENTERPRISE.value,
            credentials_path=str(actual_credentials_path),
        ),
        paths=paths,
    )
    return credentials


def _gkeepapi_login(*, paths: AppPaths) -> object:
    import gkeepapi as gkapi

    token_data = _load_master_token(paths)
    keep = gkapi.Keep()
    state = _load_gkeepapi_state(paths)
    try:
        keep.authenticate(token_data["email"], token_data["master_token"], state=state)
    except Exception as exc:
        raise AuthError(f"gkeepapi authentication failed: {exc}") from exc
    _save_gkeepapi_state(keep.dump(), paths)
    return keep


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


def logout(*, paths: AppPaths | None = None) -> bool:
    app_paths = paths or default_paths()
    removed = False
    if app_paths.token_file.exists():
        app_paths.token_file.unlink()
        removed = True
    if app_paths.gkeepapi_state_file.exists():
        app_paths.gkeepapi_state_file.unlink()
        removed = True
    return removed


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def status(*, paths: AppPaths | None = None) -> AuthStatus:
    app_paths = paths or default_paths()
    config = load_config(paths=app_paths)
    method = config.method
    if method == AuthMethod.GKEEPAPI.value:
        logged_in = app_paths.gkeepapi_state_file.exists()
    else:
        logged_in = app_paths.token_file.exists()
    return AuthStatus(
        logged_in=logged_in,
        token_path=app_paths.token_file,
        credentials_path=config.credentials_path,
        method=method,
    )


# ---------------------------------------------------------------------------
# Get credentials / build client
# ---------------------------------------------------------------------------


def get_credentials(
    *,
    paths: AppPaths | None = None,
    credentials_path: Path | None = None,
    interactive: bool = False,
) -> object:
    """Return enterprise OAuth credentials (for backward compat)."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    app_paths = paths or default_paths()
    token_file = app_paths.token_file
    if token_file.exists():
        credentials = Credentials.from_authorized_user_file(str(token_file), scopes=[KEEP_SCOPE])
        if credentials.valid:
            return credentials
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            _save_enterprise_credentials(credentials, token_file)
            return credentials
    if not interactive:
        raise AuthError("No valid OAuth token found. Run `kiko auth login` first.")
    return _enterprise_login(credentials_path, paths=app_paths)


def build_keep_client(
    *,
    paths: AppPaths | None = None,
    credentials_path: Path | None = None,
    interactive: bool = False,
    log: Callable[[str], None] = _noop,
) -> object:
    """Factory that returns the right client for the configured method."""
    app_paths = paths or default_paths()
    config = load_config(paths=app_paths)
    method = _resolve_method(config, app_paths)

    if method == AuthMethod.GKEEPAPI:
        return _build_gkeepapi_client(paths=app_paths, log=log)

    log("Authenticating with Google Keep API...")
    return _build_enterprise_client(
        credentials_path=credentials_path,
        interactive=interactive,
        paths=app_paths,
    )


def _build_enterprise_client(
    *,
    credentials_path: Path | None,
    interactive: bool,
    paths: AppPaths,
) -> object:
    from kiko.keep_client import KeepClient

    credentials = get_credentials(
        paths=paths,
        credentials_path=credentials_path,
        interactive=interactive,
    )
    return KeepClient(credentials)


def _build_gkeepapi_client(
    *,
    paths: AppPaths,
    log: Callable[[str], None] = _noop,
) -> object:
    import gkeepapi as gkapi

    from kiko.gkeepapi_client import GkeepApiClient

    token_data = _load_master_token(paths)
    log(f"Authenticating as {token_data['email']}...")
    keep = gkapi.Keep()
    state = _load_gkeepapi_state(paths)
    has_state = state is not None
    log("Syncing with Google Keep" + (" (incremental)..." if has_state else " (full sync)..."))
    try:
        keep.authenticate(token_data["email"], token_data["master_token"], state=state)
    except Exception as exc:
        raise AuthError(f"gkeepapi authentication failed: {exc}") from exc
    log("Sync complete. Saving state...")
    _save_gkeepapi_state(keep.dump(), paths)
    return GkeepApiClient(keep)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def setup(
    *,
    method: AuthMethod | None = None,
    credentials_path: Path | None = None,
    credentials_json: str | None = None,
    paths: AppPaths | None = None,
) -> SetupResult:
    app_paths = paths or default_paths()

    if method == AuthMethod.GKEEPAPI:
        return _gkeepapi_setup(
            credentials_path=credentials_path,
            credentials_json=credentials_json,
            paths=app_paths,
        )
    if method == AuthMethod.ENTERPRISE:
        return _enterprise_setup(credentials_path=credentials_path, paths=app_paths)

    # Legacy: no method specified
    return _enterprise_setup(credentials_path=credentials_path, paths=app_paths)


def _enterprise_setup(
    *,
    credentials_path: Path | None,
    paths: AppPaths,
) -> SetupResult:
    if credentials_path is not None and not credentials_path.expanduser().exists():
        raise AuthError(f"Credentials file does not exist: {credentials_path.expanduser()}")
    source_credentials = _find_optional_credentials_source(credentials_path, paths=paths)
    if source_credentials is None:
        return SetupResult(instructions=manual_enterprise_instructions(paths))
    destination = install_credentials(source_credentials, paths=paths)
    save_config(
        AuthConfig(
            method=AuthMethod.ENTERPRISE.value,
            credentials_path=str(destination),
        ),
        paths=paths,
    )
    return SetupResult(stored_credentials_path=destination)


def _gkeepapi_setup(
    *,
    credentials_path: Path | None,
    credentials_json: str | None = None,
    paths: AppPaths,
) -> SetupResult:
    token_data = _resolve_gkeepapi_token(credentials_path, credentials_json)
    if token_data is None:
        return SetupResult(instructions=manual_gkeepapi_instructions(paths))

    if "email" not in token_data or "master_token" not in token_data:
        raise AuthError(
            'Credentials must contain "email" and "master_token" keys. '
            f"Got keys: {sorted(token_data.keys())}"
        )

    paths.config_dir.mkdir(parents=True, exist_ok=True)
    destination = paths.master_token_file
    destination.write_text(
        json.dumps(token_data, indent=2) + "\n",
        encoding="utf-8",
    )

    save_config(
        AuthConfig(method=AuthMethod.GKEEPAPI.value, credentials_path=str(destination)),
        paths=paths,
    )
    return SetupResult(stored_credentials_path=destination)


def _resolve_gkeepapi_token(
    credentials_path: Path | None,
    credentials_json: str | None,
) -> dict | None:
    """Parse gkeepapi token from a JSON string, a file path, or return None."""
    if credentials_json is not None:
        try:
            return json.loads(credentials_json)
        except json.JSONDecodeError as exc:
            raise AuthError(f"Invalid JSON in --credentials value: {exc}") from exc

    if credentials_path is None:
        return None

    expanded = credentials_path.expanduser()
    if not expanded.exists():
        raise AuthError(f"Master token file does not exist: {expanded}")

    try:
        return json.loads(expanded.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise AuthError(f"Cannot read master token file: {exc}") from exc


def install_credentials(source: Path, *, paths: AppPaths | None = None) -> Path:
    app_paths = paths or default_paths()
    resolved_source = source.expanduser()
    if not resolved_source.exists():
        raise AuthError(f"Credentials file does not exist: {resolved_source}")
    app_paths.config_dir.mkdir(parents=True, exist_ok=True)
    destination = app_paths.bundled_credentials_file
    if resolved_source.resolve() != destination.resolve():
        shutil.copy2(resolved_source, destination)
    return destination


# ---------------------------------------------------------------------------
# Credentials path resolution (enterprise)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Instructions
# ---------------------------------------------------------------------------


def manual_enterprise_instructions(paths: AppPaths) -> list[str]:
    return [
        "The official Google Keep API requires a Google Workspace Enterprise subscription.",
        "If you have Enterprise, create or download a Desktop app OAuth client:",
        "",
        "The Google Keep API is enabled on the project, not on the OAuth client itself.",
        "1. Go to https://cloud.google.com/.",
        "2. Choose your Google Cloud project, or create one.",
        '3. Search for "Google Keep API", open it, and click Enable for this project.',
        '4. Search for "Google Auth platform" in the top search bar and open it.',
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
            "or rerun `kiko auth setup --method enterprise "
            "--credentials /path/to/credentials.json`."
        ),
        "9. Then run `kiko auth login`.",
        "",
        "Reference: https://developers.google.com/workspace/guides/create-credentials",
        "Reference: https://developers.google.com/workspace/guides/enable-apis",
    ]


def manual_gkeepapi_instructions(paths: AppPaths) -> list[str]:
    docker_cmd = (
        "docker run --rm -it python:3 sh -c '"
        'pip install -q gpsoauth && python3 -c "'
        "import gpsoauth,secrets,json;"
        "e=input(\\\"Email: \\\");"
        "t=input(\\\"OAuth token: \\\");"
        "a=secrets.token_hex(8);"
        "r=gpsoauth.exchange_token(e,t,a);"
        "print(json.dumps({\\\"email\\\":e,\\\"master_token\\\":r[\\\"Token\\\"]}))"
        "\"'"
    )
    return [
        "gkeepapi uses the unofficial mobile Google Keep API.",
        "It works with any Google account (no Enterprise subscription needed).",
        "",
        "Step 1 - Get an OAuth token cookie:",
        "  a. Open https://accounts.google.com/EmbeddedSetup in your browser.",
        "  b. Log in with your Google account and click 'I agree'.",
        "     (The page may show a loading screen forever; ignore it.)",
        "  c. Open browser developer tools -> Application -> Cookies.",
        "  d. Copy the value of the `oauth_token` cookie.",
        "",
        "Step 2 - Get a master token (outputs JSON):",
        f"  {docker_cmd}",
        "",
        "Step 3 - Pass the JSON output directly to kiko:",
        "  kiko auth setup --method gkeepapi --credentials '{...json from step 2...}'",
        "",
        "  Or save it to a file and pass the path:",
        "  kiko auth setup --method gkeepapi --credentials /path/to/token.json",
        "",
        "Then run `kiko auth login` to verify the token works.",
        "",
        "Reference: https://github.com/simon-weber/gpsoauth#alternative-flow",
        "Reference: https://gkeepapi.readthedocs.io/en/latest/",
    ]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_method(config: AuthConfig, paths: AppPaths) -> AuthMethod:
    if config.method == AuthMethod.GKEEPAPI.value:
        return AuthMethod.GKEEPAPI
    if config.method == AuthMethod.ENTERPRISE.value:
        return AuthMethod.ENTERPRISE
    # Auto-detect from existing artifacts
    if paths.master_token_file.exists():
        return AuthMethod.GKEEPAPI
    return AuthMethod.ENTERPRISE


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


def _save_enterprise_credentials(credentials: object, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(credentials.to_json(), encoding="utf-8")  # type: ignore[union-attr]


def _load_master_token(paths: AppPaths) -> dict[str, str]:
    if not paths.master_token_file.exists():
        raise AuthError(
            "No master token file found. "
            "Run `kiko auth setup --method gkeepapi --credentials /path/to/token.json` first."
        )
    try:
        data = json.loads(paths.master_token_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise AuthError(f"Cannot read master token file: {exc}") from exc
    if "email" not in data or "master_token" not in data:
        raise AuthError("Master token file missing 'email' or 'master_token' keys.")
    return data


def _load_gkeepapi_state(paths: AppPaths) -> dict | None:
    if not paths.gkeepapi_state_file.exists():
        return None
    try:
        return json.loads(paths.gkeepapi_state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_gkeepapi_state(state: dict, paths: AppPaths) -> None:
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    paths.gkeepapi_state_file.write_text(
        json.dumps(state),
        encoding="utf-8",
    )
