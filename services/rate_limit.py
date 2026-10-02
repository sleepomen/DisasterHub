"""
按來源計算的請求節流（滑動視窗）。

/api/chat 的每一題都會佔用一條工作執行緒與一次 LLM 生成（最長 OLLAMA_TIMEOUT 秒），
而它沒有登入保護：部署出去之後任何連得到網域的人都能無限呼叫。
全域的併發名額（CHAT_MAX_CONCURRENT）只擋「同時幾個」，擋不住同一個人連續問一整天，
所以再加一層「單一來源每段時間最多幾次」。只放記憶體，重啟即清空，對單機部署夠用。
"""
import math
import threading
import time
from collections import deque

# 來源表的硬上限：有人拿大量不同來源灑請求時，記憶體不能跟著無限長
MAX_TRACKED_SOURCES = 10_000


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: float, max_tracked: int = MAX_TRACKED_SOURCES):
        self.max_requests = max_requests
        self.window_seconds = float(window_seconds)
        self.max_tracked = max_tracked
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        """max_requests <= 0 代表停用（給不需要節流的部署用）"""
        return self.max_requests > 0

    def tracked(self) -> int:
        with self._lock:
            return len(self._hits)

    def _prune(self, now: float) -> None:
        """要在持鎖狀態下呼叫。丟掉整個視窗都沒動靜的來源；仍超過硬上限就砍最舊的"""
        stale = [key for key, hits in self._hits.items() if not hits or hits[-1] + self.window_seconds <= now]
        for key in stale:
            del self._hits[key]
        overflow = len(self._hits) - self.max_tracked
        if overflow > 0:
            oldest = sorted(self._hits, key=lambda k: self._hits[k][-1])[:overflow]
            for key in oldest:
                del self._hits[key]

    def hit(self, key: str, now: float | None = None) -> int:
        """
        記下一次請求，回傳「還要等幾秒」；0 代表放行。
        檢查與記錄在同一把鎖裡做完，兩個請求同時進來才不會雙雙放行。
        被拒絕的請求不計入視窗，否則一直猛打的來源永遠等不到解除。
        """
        if not self.enabled:
            return 0
        now = time.time() if now is None else now
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            cutoff = now - self.window_seconds
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.max_requests:
                # 最舊的那一次離開視窗，就空出一個名額
                wait = hits[0] + self.window_seconds - now
                return max(1, math.ceil(wait))
            hits.append(now)
            self._prune(now)
            return 0

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)
