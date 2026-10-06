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


def test_validate_rejects_out_of_range_numbers(monkeypatch):
    config = load_config(
        monkeypatch,
        POSTGRES_PASSWORD="pw", SYNC_API_KEY="a-long-random-key", WRITE_API_KEY=None,
        ADMIN_USERNAME="ops", ADMIN_PASSWORD="pw", SESSION_SECRET="a-long-random-secret",
        CHROMA_PATH="/data/chroma",
        SESSION_HOURS="0", DB_POOL_MIN="5", DB_POOL_MAX="2", RAG_TOP_K="0", CHAT_MAX_CONCURRENT="0",
    )
    with pytest.raises(config.ConfigError) as exc:
        config.validate()
    message = str(exc.value)
    for key in ("SESSION_HOURS", "DB_POOL_MAX", "RAG_TOP_K", "CHAT_MAX_CONCURRENT"):
        assert key in message


def test_unparseable_numbers_fall_back_and_warn(monkeypatch):
    # 打錯字的數值不該讓服務起不來，但要在啟動 log 提醒設定被忽略了
    config = load_config(
        monkeypatch,
        POSTGRES_PASSWORD="pw", SYNC_API_KEY="a-long-random-key", WRITE_API_KEY=None,
        ADMIN_USERNAME="ops", ADMIN_PASSWORD="pw", SESSION_SECRET="a-long-random-secret",
        CHROMA_PATH="/data/chroma",
        RAG_TOP_K="ten", TRUST_PROXY_HEADERS="maybe",
    )
    assert config.RAG_TOP_K == 10
    assert config.TRUST_PROXY_HEADERS is False
    warnings = config.validate()
    assert any("RAG_TOP_K" in w and "預設值" in w for w in warnings)
    assert any("TRUST_PROXY_HEADERS" in w for w in warnings)


def test_bool_setting_accepts_common_spellings(monkeypatch):
    assert load_config(monkeypatch, TRUST_PROXY_HEADERS="true").TRUST_PROXY_HEADERS is True
    assert load_config(monkeypatch, TRUST_PROXY_HEADERS="1").TRUST_PROXY_HEADERS is True
    assert load_config(monkeypatch, TRUST_PROXY_HEADERS="off").TRUST_PROXY_HEADERS is False
    assert load_config(monkeypatch, TRUST_PROXY_HEADERS=None).TRUST_PROXY_HEADERS is False


def test_validate_rejects_bad_distance_threshold(monkeypatch):
    config = load_config(monkeypatch, POSTGRES_PASSWORD="pw", RAG_MAX_DISTANCE="0")
    with pytest.raises(config.ConfigError) as exc:
        config.validate()
    assert "RAG_MAX_DISTANCE" in str(exc.value)
