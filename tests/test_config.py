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
        SYNC_API_KEY="a-long-random-key", CHROMA_PATH="/data/chroma",
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
        CHROMA_PATH="",
    )
    warnings = config.validate()
    assert len(warnings) == 3
    assert any("POSTGRES_PASSWORD" in w for w in warnings)
    assert any("SYNC_API_KEY" in w for w in warnings)
    assert any("CHROMA_PATH" in w for w in warnings)
