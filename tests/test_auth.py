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


def test_logout_removes_token(tmp_path: Path) -> None:
    paths = auth.AppPaths(
        config_dir=tmp_path,
        config_file=tmp_path / "config.json",
        token_file=tmp_path / "oauth-token.json",
    )
    paths.token_file.write_text("token", encoding="utf-8")
    assert auth.logout(paths=paths) is True
    assert not paths.token_file.exists()
