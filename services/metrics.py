"""
線上計數器與延遲取樣。

離線評測（evals/）量的是「模型在那 130 道題上的表現」；這裡量的是「真實使用者問了什麼、
系統實際怎麼回」。兩者不會自動一致，而原本線上一個數字都沒有：不知道有多少問題在規則層
篩選後落空、有多少被距離門檻擋成「沒有相關資料」、有多少吃到 429、生成延遲的分布如何。

只放記憶體、不引入任何外部依賴（刻意不用 prometheus client），重啟歸零，對單機部署夠用。
"""
import math
import threading
import time
from collections import deque

# 每個延遲名稱最多保留幾筆樣本（算百分位用），更舊的丟掉
SAMPLE_SIZE = 512
# 計數器名稱都是程式裡寫死的常數，正常不會成長；設上限是防止將來有人把使用者輸入當成名稱
MAX_KEYS = 200

# 路由名稱的前綴，計數器名稱在 chat_service 裡登記
ROUTE_PREFIX = "chat.route."


def _percentile(values: list[float], fraction: float) -> float:
    """最近排名法（nearest-rank）；values 必須已排序且非空"""
    index = min(len(values) - 1, max(0, math.ceil(fraction * len(values)) - 1))
    return values[index]


class Metrics:
    def __init__(self, sample_size: int = SAMPLE_SIZE, max_keys: int = MAX_KEYS):
        self.sample_size = sample_size
        self.max_keys = max_keys
        self.started_at = time.time()
        self._counters: dict[str, int] = {}
        self._samples: dict[str, deque] = {}
        self._lock = threading.Lock()
        # 一次問答的整條鏈（build_context → 檢索 → 生成）都跑在同一條工作執行緒上，
        # 所以路由用 thread-local 傳給最後那行摘要日誌就夠，不需要 contextvars
        self._local = threading.local()

    def register(self, *names: str) -> None:
        """
        預先登記計數器名稱，讓它們在還沒發生過時就以 0 出現在 snapshot 裡。
        沒登記的話「從未發生」和「沒有這個指標」在輸出上長得一樣，
        而 fell_back 這種指標的意義就在於「佔總數的幾分之幾」。
        """
        with self._lock:
            for name in names:
                if name not in self._counters and len(self._counters) < self.max_keys:
                    self._counters[name] = 0

    def incr(self, name: str, amount: int = 1) -> None:
        with self._lock:
            if name in self._counters:
                self._counters[name] += amount
            elif len(self._counters) < self.max_keys:
                self._counters[name] = amount

    def route(self, name: str) -> None:
        """記下這次問答走的路由：計數器用來看分布，thread-local 給摘要日誌引用"""
        self._local.route = name
        self.incr(ROUTE_PREFIX + name)

    def last_route(self) -> str:
        """本執行緒最近一次 route() 記下的名稱；沒有就回 '-'"""
        return getattr(self._local, "route", "-")

    def observe(self, name: str, value_ms: float) -> None:
        with self._lock:
            samples = self._samples.get(name)
            if samples is None:
                if len(self._samples) >= self.max_keys:
                    return
                samples = self._samples[name] = deque(maxlen=self.sample_size)
            samples.append(float(value_ms))

    def snapshot(self) -> dict:
        with self._lock:
            counters = dict(self._counters)
            samples = {name: sorted(values) for name, values in self._samples.items() if values}
            started_at = self.started_at
        latency = {
            name: {
                "count": len(values),
                "p50": round(_percentile(values, 0.50), 1),
                "p95": round(_percentile(values, 0.95), 1),
                "max": round(values[-1], 1),
            }
            for name, values in samples.items()
        }
        return {
            "uptime_s": round(time.time() - started_at, 1),
            "counters": counters,
            # 取樣上限 SAMPLE_SIZE 筆，所以百分位是「最近這些請求」的，不是開機以來全部
            "latency_ms": latency,
        }

    def reset(self) -> None:
        """測試用：清掉所有計數與樣本，但保留已登記的名稱（歸零而非消失）"""
        with self._lock:
            self._counters = dict.fromkeys(self._counters, 0)
            self._samples.clear()
            self.started_at = time.time()


# 全域單例：服務層直接 import 使用，跟 config / logger 的用法一致
metrics = Metrics()
