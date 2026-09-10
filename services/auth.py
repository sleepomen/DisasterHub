"""
登入與 session：單一管理帳號放在環境變數，session 是 HMAC 簽章的 cookie。

寫入端點（模擬 / 收容人數回寫 / 重置）會改資料庫與全域狀態，部署出去之後
任何連得到網域的人都能直接呼叫，所以要先登入。cookie 由瀏覽器自動帶，
操作員開網站登入一次，之後整個班次不必再輸入任何東西。
只用標準函式庫，不另外加套件。
"""
import base64
import hashlib
import hmac
import logging
import secrets
import threading
import time

import config

logger = logging.getLogger(__name__)

COOKIE_NAME = "disasterhub_session"

# 登入失敗節流：同一來源連續失敗達上限後，冷卻時間內一律拒絕
MAX_FAILURES = 5
LOCKOUT_SECONDS = 300
# 失敗紀錄表的硬上限：有人拿大量不同來源灑密碼時，記憶體不能跟著無限長
MAX_TRACKED_SOURCES = 10_000


def credentials_configured() -> bool:
    return bool(config.ADMIN_USERNAME) and bool(config.ADMIN_PASSWORD)


def check_credentials(username: str, password: str) -> bool:
    """帳號與密碼都用固定時間比對，避免從回應時間猜出正確長度或前綴"""
    if not credentials_configured():
        return False
    user_ok = hmac.compare_digest(username.encode("utf-8"), config.ADMIN_USERNAME.encode("utf-8"))
    pass_ok = hmac.compare_digest(password.encode("utf-8"), config.ADMIN_PASSWORD.encode("utf-8"))
    return user_ok and pass_ok


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


class SessionManager:
    """
    token 格式：<base64(username)>.<到期時間戳>.<HMAC-SHA256>
    伺服器不用存 session，重啟後只要 secret 沒變，既有 cookie 仍然有效。
    """

    def __init__(self, secret: str | None = None, hours: float | None = None):
        configured = secret if secret is not None else config.SESSION_SECRET
        self.generated = not configured
        self.secret = (configured or secrets.token_urlsafe(32)).encode("utf-8")
        self.hours = float(hours if hours is not None else config.SESSION_HOURS)
        if self.generated:
            logger.warning("未設定 SESSION_SECRET，本次啟動隨機產生；服務重啟後所有登入都會失效")

    @property
    def max_age(self) -> int:
        return int(self.hours * 3600)

    def _sign(self, body: str) -> str:
        return hmac.new(self.secret, body.encode("utf-8"), hashlib.sha256).hexdigest()

    def issue(self, username: str, now: float | None = None) -> str:
        now = time.time() if now is None else now
        body = f"{_b64encode(username.encode('utf-8'))}.{int(now + self.max_age)}"
        return f"{body}.{self._sign(body)}"

    def verify(self, token: str | None, now: float | None = None) -> str | None:
        """合法且未過期就回使用者名稱，否則回 None。任何格式錯誤都當成無效，不拋例外"""
        if not token:
            return None
        parts = token.split(".")
        if len(parts) != 3:
            return None
        user_part, expiry_part, signature = parts
        body = f"{user_part}.{expiry_part}"
        if not hmac.compare_digest(self._sign(body), signature):
            return None
        try:
            expiry = int(expiry_part)
            username = _b64decode(user_part).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
        now = time.time() if now is None else now
        if now >= expiry:
            return None
        return username or None


class LoginThrottle:
    """
    以來源位址計算連續失敗次數。達上限後在冷卻時間內直接拒絕，
    成功登入或冷卻結束就歸零。只放記憶體，重啟即清空，對單機部署夠用。
    每次記錄失敗都會順手清掉已經過了冷卻時間的舊紀錄，表的大小只跟最近一個冷卻週期的來源數有關。
    """

    def __init__(
        self,
        max_failures: int = MAX_FAILURES,
        lockout_seconds: int = LOCKOUT_SECONDS,
        max_tracked: int = MAX_TRACKED_SOURCES,
    ):
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self.max_tracked = max_tracked
        self._failures: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def tracked(self) -> int:
        with self._lock:
            return len(self._failures)

    def _prune(self, now: float) -> None:
        """要在持鎖狀態下呼叫。丟掉過期的紀錄；仍然超過硬上限就把最舊的砍掉"""
        expired = [key for key, (_, last) in self._failures.items() if last + self.lockout_seconds <= now]
        for key in expired:
            del self._failures[key]
        overflow = len(self._failures) - self.max_tracked
        if overflow > 0:
            oldest = sorted(self._failures, key=lambda k: self._failures[k][1])[:overflow]
            for key in oldest:
                del self._failures[key]

    def retry_after(self, key: str, now: float | None = None) -> int:
        """尚在鎖定中回剩餘秒數，否則回 0"""
        now = time.time() if now is None else now
        with self._lock:
            entry = self._failures.get(key)
            if not entry:
                return 0
            count, last = entry
            if count < self.max_failures:
                return 0
            remaining = int(last + self.lockout_seconds - now)
            if remaining <= 0:
                del self._failures[key]
                return 0
            return remaining

    def record_failure(self, key: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            count, _ = self._failures.get(key, (0, now))
            self._failures[key] = (count + 1, now)
            self._prune(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
