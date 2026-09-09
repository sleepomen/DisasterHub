import importlib
import pytest


def load_config(monkeypatch, **env):
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    import config
    return importlib.reload(config)


@pytest.fixture(autouse=True)
def restore_config():
    yield
    import config
    importlib.reload(config)


def test_validate_passes_with_full_settings(monkeypatch):
    config = load_config(
        monkeypatch,
        POSTGRES_DB="db", POSTGRES_USER="u", POSTGRES_PASSWORD="pw",
        POSTGRES_HOST="h", POSTGRES_PORT="5432",
        SYNC_API_KEY="a-long-random-key", WRITE_API_KEY="another-long-random-key",
        ADMIN_USERNAME="ops", ADMIN_PASSWORD="pw", SESSION_SECRET="a-long-random-secret",
        CHROMA_PATH="/data/chroma",
    )
    assert config.validate() == []


def test_validate_rejects_missing_password(monkeypatch):
    config = load_config(monkeypatch, POSTGRES_PASSWORD="")
    with pytest.raises(config.ConfigError) as exc:
        config.validate()
    assert "POSTGRES_PASSWORD" in str(exc.value)


def test_validate_rejects_blank_password(monkeypatch):
    config = load_config(monkeypatch, POSTGRES_PASSWORD="   ")
    with pytest.raises(config.ConfigError):
        config.validate()


def test_validate_warns_about_placeholders(monkeypatch):
    config = load_config(
        monkeypatch,
        POSTGRES_PASSWORD="change_me",
        SYNC_API_KEY="change_me_to_a_long_random_string",
        WRITE_API_KEY="change_me_to_another_long_random_string",
        ADMIN_USERNAME="admin", ADMIN_PASSWORD="change_me",
        SESSION_SECRET="change_me_to_a_long_random_string",
        CHROMA_PATH="",
    )
    warnings = config.validate()
    assert len(warnings) == 6
    assert any("POSTGRES_PASSWORD" in w for w in warnings)
    assert any("SYNC_API_KEY" in w for w in warnings)
    assert any("ADMIN_PASSWORD" in w for w in warnings)
    assert any("SESSION_SECRET" in w for w in warnings)
    assert any("WRITE_API_KEY" in w and "預設值" in w for w in warnings)
    assert any("CHROMA_PATH" in w for w in warnings)


def test_validate_warns_when_admin_account_missing(monkeypatch):
    # WRITE_API_KEY 是選配，留空不警告；帳密沒設才要提醒
    config = load_config(
        monkeypatch,
        POSTGRES_PASSWORD="pw", SYNC_API_KEY="a-long-random-key", WRITE_API_KEY=None,
        ADMIN_USERNAME=None, ADMIN_PASSWORD=None, SESSION_SECRET="a-long-random-secret",
        CHROMA_PATH="/data/chroma",
    )
    warnings = config.validate()
    assert len(warnings) == 1
    assert "ADMIN_USERNAME" in warnings[0]


def test_validate_warns_when_session_secret_missing(monkeypatch):
    config = load_config(
        monkeypatch,
        POSTGRES_PASSWORD="pw", SYNC_API_KEY="a-long-random-key", WRITE_API_KEY=None,
        ADMIN_USERNAME="ops", ADMIN_PASSWORD="pw", SESSION_SECRET=None,
        CHROMA_PATH="/data/chroma",
    )
    warnings = config.validate()
    assert len(warnings) == 1
    assert "SESSION_SECRET" in warnings[0] and "重來" in warnings[0]
