from __future__ import annotations

import json
from pathlib import Path

import pytest

from kiko import auth


class FakeCredentials:
    valid = True
    expired = False
    refresh_token = None

    def to_json(self) -> str:
        return '{"token":"abc"}'


class FakeFlow:
    def run_local_server(self, *, open_browser: bool, port: int) -> FakeCredentials:
        assert port == 0
        assert open_browser is False
        return FakeCredentials()


def _patch_flow(monkeypatch) -> None:
    fake = type(
        "FakeFlowModule",
        (),
        {"from_client_secrets_file": staticmethod(lambda path, scopes: FakeFlow())},
    )
    monkeypatch.setattr("google_auth_oauthlib.flow.InstalledAppFlow", fake)


def _enterprise_paths(tmp_path: Path) -> auth.AppPaths:
    return auth.AppPaths(
        config_dir=tmp_path,
        config_file=tmp_path / "config.json",
        token_file=tmp_path / "oauth-token.json",
    )


# ---------------------------------------------------------------------------
# Enterprise auth tests
# ---------------------------------------------------------------------------


def test_login_saves_token_and_config(tmp_path: Path, monkeypatch) -> None:
    paths = _enterprise_paths(tmp_path)
    credentials_file = tmp_path / "credentials.json"
    credentials_file.write_text("{}", encoding="utf-8")
    auth.save_config(auth.AuthConfig(method="enterprise"), paths=paths)
    _patch_flow(monkeypatch)

    auth.login(credentials_file, paths=paths, open_browser=False)

    assert paths.token_file.exists()
    config = auth.load_config(paths=paths)
    assert config.credentials_path == str(credentials_file)
    assert config.method == "enterprise"


def test_login_uses_saved_credentials_path_when_omitted(tmp_path: Path, monkeypatch) -> None:
    paths = _enterprise_paths(tmp_path)
    credentials_file = tmp_path / "saved-credentials.json"
    credentials_file.write_text("{}", encoding="utf-8")
    auth.save_config(
        auth.AuthConfig(method="enterprise", credentials_path=str(credentials_file)),
        paths=paths,
    )
    _patch_flow(monkeypatch)

    auth.login(paths=paths, open_browser=False)

    assert paths.token_file.exists()


def test_login_uses_default_credentials_file_when_present(tmp_path: Path, monkeypatch) -> None:
    paths = _enterprise_paths(tmp_path)
    paths.bundled_credentials_file.write_text("{}", encoding="utf-8")
    _patch_flow(monkeypatch)

    auth.login(paths=paths, open_browser=False)

    config = auth.load_config(paths=paths)
    assert config.credentials_path == str(paths.bundled_credentials_file)


def test_logout_removes_token(tmp_path: Path) -> None:
    paths = _enterprise_paths(tmp_path)
    paths.token_file.write_text("token", encoding="utf-8")
    assert auth.logout(paths=paths) is True
    assert not paths.token_file.exists()


def test_logout_removes_gkeepapi_state(tmp_path: Path) -> None:
    paths = _enterprise_paths(tmp_path)
    paths.gkeepapi_state_file.write_text("{}", encoding="utf-8")
    assert auth.logout(paths=paths) is True
    assert not paths.gkeepapi_state_file.exists()


def test_install_credentials_copies_into_auth_dir(tmp_path: Path) -> None:
    paths = auth.AppPaths(
        config_dir=tmp_path / "cfg",
        config_file=tmp_path / "cfg" / "config.json",
        token_file=tmp_path / "cfg" / "oauth-token.json",
    )
    source = tmp_path / "downloaded.json"
    source.write_text("{}", encoding="utf-8")

    destination = auth.install_credentials(source, paths=paths)

    assert destination == paths.bundled_credentials_file
    assert destination.exists()


def test_setup_enterprise_stores_credentials(tmp_path: Path) -> None:
    paths = auth.AppPaths(
        config_dir=tmp_path / "cfg",
        config_file=tmp_path / "cfg" / "config.json",
        token_file=tmp_path / "cfg" / "oauth-token.json",
    )
    source = tmp_path / "credentials.json"
    source.write_text("{}", encoding="utf-8")

    result = auth.setup(method=auth.AuthMethod.ENTERPRISE, credentials_path=source, paths=paths)

    assert result.stored_credentials_path == paths.bundled_credentials_file
    assert paths.bundled_credentials_file.exists()
    config = auth.load_config(paths=paths)
    assert config.method == "enterprise"


def test_setup_enterprise_returns_instructions_when_missing(tmp_path: Path) -> None:
    paths = auth.AppPaths(
        config_dir=tmp_path / "cfg",
        config_file=tmp_path / "cfg" / "config.json",
        token_file=tmp_path / "cfg" / "oauth-token.json",
    )

    result = auth.setup(method=auth.AuthMethod.ENTERPRISE, paths=paths)

    assert result.stored_credentials_path is None
    assert any("Enterprise" in line for line in result.instructions)


# ---------------------------------------------------------------------------
# gkeepapi auth tests
# ---------------------------------------------------------------------------


def test_setup_gkeepapi_installs_master_token(tmp_path: Path) -> None:
    paths = _enterprise_paths(tmp_path)
    token_file = tmp_path / "my-token.json"
    token_file.write_text(
        json.dumps({"email": "a@b.com", "master_token": "aas_et/xxx"}),
        encoding="utf-8",
    )

    result = auth.setup(
        method=auth.AuthMethod.GKEEPAPI,
        credentials_path=token_file,
        paths=paths,
    )

    assert result.stored_credentials_path == paths.master_token_file
    assert paths.master_token_file.exists()
    config = auth.load_config(paths=paths)
    assert config.method == "gkeepapi"


def test_setup_gkeepapi_validates_token_format(tmp_path: Path) -> None:
    paths = _enterprise_paths(tmp_path)
    bad_file = tmp_path / "bad.json"
    bad_file.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")

    with pytest.raises(auth.AuthError, match="email"):
        auth.setup(
            method=auth.AuthMethod.GKEEPAPI,
            credentials_path=bad_file,
            paths=paths,
        )


def test_setup_gkeepapi_accepts_json_string(tmp_path: Path) -> None:
    paths = _enterprise_paths(tmp_path)
    raw = '{"email": "u@g.com", "master_token": "aas_et/yyy"}'

    result = auth.setup(
        method=auth.AuthMethod.GKEEPAPI,
        credentials_json=raw,
        paths=paths,
    )

    assert result.stored_credentials_path == paths.master_token_file
    stored = json.loads(paths.master_token_file.read_text(encoding="utf-8"))
    assert stored["email"] == "u@g.com"
    assert stored["master_token"] == "aas_et/yyy"


def test_setup_gkeepapi_rejects_invalid_json_string(tmp_path: Path) -> None:
    paths = _enterprise_paths(tmp_path)

    with pytest.raises(auth.AuthError, match="Invalid JSON"):
        auth.setup(
            method=auth.AuthMethod.GKEEPAPI,
            credentials_json="not json",
            paths=paths,
        )


def test_setup_gkeepapi_prints_instructions_when_no_credentials(tmp_path: Path) -> None:
    paths = _enterprise_paths(tmp_path)

    result = auth.setup(method=auth.AuthMethod.GKEEPAPI, paths=paths)

    assert result.stored_credentials_path is None
    assert any("gpsoauth" in line for line in result.instructions)


def test_config_persists_method(tmp_path: Path) -> None:
    paths = _enterprise_paths(tmp_path)
    auth.save_config(auth.AuthConfig(method="gkeepapi", credentials_path="/x"), paths=paths)
    loaded = auth.load_config(paths=paths)
    assert loaded.method == "gkeepapi"
    assert loaded.credentials_path == "/x"


def test_legacy_config_without_method_returns_none(tmp_path: Path) -> None:
    paths = _enterprise_paths(tmp_path)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    paths.config_file.write_text(
        json.dumps({"credentials_path": "/old"}),
        encoding="utf-8",
    )
    loaded = auth.load_config(paths=paths)
    assert loaded.method is None
    assert loaded.credentials_path == "/old"


def test_resolve_method_autodetects_gkeepapi(tmp_path: Path) -> None:
    paths = _enterprise_paths(tmp_path)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    paths.master_token_file.write_text("{}", encoding="utf-8")
    config = auth.AuthConfig()
    assert auth._resolve_method(config, paths) == auth.AuthMethod.GKEEPAPI


def test_resolve_method_defaults_to_enterprise(tmp_path: Path) -> None:
    paths = _enterprise_paths(tmp_path)
    config = auth.AuthConfig()
    assert auth._resolve_method(config, paths) == auth.AuthMethod.ENTERPRISE


def test_status_shows_method(tmp_path: Path) -> None:
    paths = _enterprise_paths(tmp_path)
    auth.save_config(auth.AuthConfig(method="gkeepapi"), paths=paths)
    paths.master_token_file.write_text("{}", encoding="utf-8")
    s = auth.status(paths=paths)
    assert s.method == "gkeepapi"
    # master token exists but no state file → not logged in yet
    assert s.logged_in is False


def test_status_gkeepapi_logged_in_after_login(tmp_path: Path) -> None:
    paths = _enterprise_paths(tmp_path)
    auth.save_config(auth.AuthConfig(method="gkeepapi"), paths=paths)
    paths.master_token_file.write_text("{}", encoding="utf-8")
    paths.gkeepapi_state_file.write_text("{}", encoding="utf-8")
    s = auth.status(paths=paths)
    assert s.method == "gkeepapi"
    assert s.logged_in is True
