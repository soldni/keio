from __future__ import annotations

from pathlib import Path

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


def test_login_saves_token_and_config(tmp_path: Path, monkeypatch) -> None:
    paths = auth.AppPaths(
        config_dir=tmp_path,
        config_file=tmp_path / "config.json",
        token_file=tmp_path / "oauth-token.json",
    )
    credentials_file = tmp_path / "credentials.json"
    credentials_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        auth.InstalledAppFlow,
        "from_client_secrets_file",
        lambda path, scopes: FakeFlow(),
    )

    auth.login(credentials_file, paths=paths, open_browser=False)

    assert paths.token_file.exists()
    config = auth.load_config(paths=paths)
    assert config.credentials_path == str(credentials_file)


def test_login_uses_saved_credentials_path_when_omitted(tmp_path: Path, monkeypatch) -> None:
    paths = auth.AppPaths(
        config_dir=tmp_path,
        config_file=tmp_path / "config.json",
        token_file=tmp_path / "oauth-token.json",
    )
    credentials_file = tmp_path / "saved-credentials.json"
    credentials_file.write_text("{}", encoding="utf-8")
    auth.save_config(auth.AuthConfig(credentials_path=str(credentials_file)), paths=paths)
    monkeypatch.setattr(
        auth.InstalledAppFlow,
        "from_client_secrets_file",
        lambda path, scopes: FakeFlow(),
    )

    auth.login(paths=paths, open_browser=False)

    assert paths.token_file.exists()


def test_login_uses_default_credentials_file_when_present(tmp_path: Path, monkeypatch) -> None:
    paths = auth.AppPaths(
        config_dir=tmp_path,
        config_file=tmp_path / "config.json",
        token_file=tmp_path / "oauth-token.json",
    )
    paths.bundled_credentials_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        auth.InstalledAppFlow,
        "from_client_secrets_file",
        lambda path, scopes: FakeFlow(),
    )

    auth.login(paths=paths, open_browser=False)

    config = auth.load_config(paths=paths)
    assert config.credentials_path == str(paths.bundled_credentials_file)


def test_logout_removes_token(tmp_path: Path) -> None:
    paths = auth.AppPaths(
        config_dir=tmp_path,
        config_file=tmp_path / "config.json",
        token_file=tmp_path / "oauth-token.json",
    )
    paths.token_file.write_text("token", encoding="utf-8")
    assert auth.logout(paths=paths) is True
    assert not paths.token_file.exists()


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
    assert auth.load_config(paths=paths).credentials_path == str(destination)


def test_setup_uses_gcloud_project_and_stores_credentials(tmp_path: Path, monkeypatch) -> None:
    paths = auth.AppPaths(
        config_dir=tmp_path / "cfg",
        config_file=tmp_path / "cfg" / "config.json",
        token_file=tmp_path / "cfg" / "oauth-token.json",
    )
    source = tmp_path / "credentials.json"
    source.write_text("{}", encoding="utf-8")

    result = auth.setup(credentials_path=source, paths=paths)

    assert result.stored_credentials_path == paths.bundled_credentials_file
    assert paths.bundled_credentials_file.exists()


def test_setup_returns_manual_instructions_when_credentials_missing(tmp_path: Path) -> None:
    paths = auth.AppPaths(
        config_dir=tmp_path / "cfg",
        config_file=tmp_path / "cfg" / "config.json",
        token_file=tmp_path / "cfg" / "oauth-token.json",
    )

    result = auth.setup(paths=paths)

    assert result.stored_credentials_path is None
    assert any("Desktop app OAuth client" in line for line in result.instructions)
