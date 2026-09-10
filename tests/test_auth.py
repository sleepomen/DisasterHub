from unittest.mock import patch

from services import auth
from services.auth import LoginThrottle, SessionManager


def test_session_roundtrip():
    sm = SessionManager(secret="s3cret", hours=1)
    token = sm.issue("ops", now=1_000_000)
    assert sm.verify(token, now=1_000_000 + 3599) == "ops"


def test_session_expires():
    sm = SessionManager(secret="s3cret", hours=1)
    token = sm.issue("ops", now=1_000_000)
    assert sm.verify(token, now=1_000_000 + 3600) is None


def test_session_rejects_tampering():
    sm = SessionManager(secret="s3cret", hours=1)
    token = sm.issue("ops", now=1_000_000)
    user_part, expiry, sig = token.split(".")
    # 改到期時間、改使用者、改簽章、格式錯誤都要拒絕，而且不能拋例外
    assert sm.verify(f"{user_part}.{int(expiry) + 99999}.{sig}", now=1_000_000) is None
    assert sm.verify(f"{sm.issue('admin', now=1_000_000).split('.')[0]}.{expiry}.{sig}", now=1_000_000) is None
    assert sm.verify(f"{user_part}.{expiry}.{'0' * 64}", now=1_000_000) is None
    assert sm.verify("garbage", now=1_000_000) is None
    assert sm.verify("", now=1_000_000) is None
    assert sm.verify(None, now=1_000_000) is None


def test_session_bound_to_secret():
    token = SessionManager(secret="one", hours=1).issue("ops")
    assert SessionManager(secret="two", hours=1).verify(token) is None


def test_session_generates_secret_when_unset():
    with patch("config.SESSION_SECRET", ""):
        sm = SessionManager()
    assert sm.generated is True
    assert sm.verify(sm.issue("ops")) == "ops"


def test_username_with_dots_and_unicode_survives():
    sm = SessionManager(secret="s3cret", hours=1)
    assert sm.verify(sm.issue("值班.主管")) == "值班.主管"


def test_check_credentials_requires_both_to_match():
    with patch("config.ADMIN_USERNAME", "ops"), patch("config.ADMIN_PASSWORD", "pw"):
        assert auth.credentials_configured()
        assert auth.check_credentials("ops", "pw")
        assert not auth.check_credentials("ops", "PW")
        assert not auth.check_credentials("admin", "pw")
    with patch("config.ADMIN_USERNAME", ""), patch("config.ADMIN_PASSWORD", "pw"):
        assert not auth.credentials_configured()
        assert not auth.check_credentials("", "pw")


def test_throttle_locks_after_max_failures_and_recovers():
    t = LoginThrottle(max_failures=3, lockout_seconds=60)
    for i in range(3):
        assert t.retry_after("1.2.3.4", now=100 + i) == 0
        t.record_failure("1.2.3.4", now=100 + i)
    assert t.retry_after("1.2.3.4", now=103) == 60 - 1
    assert t.retry_after("5.6.7.8", now=103) == 0
    # 冷卻結束自動解鎖
    assert t.retry_after("1.2.3.4", now=102 + 60) == 0
    # 成功登入直接歸零
    t.record_failure("1.2.3.4", now=200)
    t.reset("1.2.3.4")
    assert t.retry_after("1.2.3.4", now=200) == 0


def test_throttle_prunes_expired_sources():
    # 有人拿大量不同來源灑密碼時，過了冷卻時間的紀錄要清掉，表不能無限長
    t = LoginThrottle(max_failures=3, lockout_seconds=60)
    for i in range(500):
        t.record_failure(f"10.0.0.{i}", now=100)
    assert t.tracked() == 500
    t.record_failure("fresh", now=100 + 60)
    assert t.tracked() == 1


def test_throttle_enforces_hard_cap():
    t = LoginThrottle(max_failures=3, lockout_seconds=3600, max_tracked=100)
    for i in range(150):
        t.record_failure(f"src-{i}", now=100 + i)
    assert t.tracked() == 100
    # 最舊的被砍掉，最新的還在
    assert t.retry_after("src-0", now=300) == 0
    t.record_failure("src-149", now=300)
    t.record_failure("src-149", now=300)
    assert t.retry_after("src-149", now=300) > 0
