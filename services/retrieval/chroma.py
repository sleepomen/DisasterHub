"""
建立 ChromaDB client 的唯一入口：關掉它內建的使用統計（telemetry）。

ChromaDB 0.5 每次建立 client、查詢都會呼叫 posthog 送匿名統計。映像裡的 posthog 是 7.x，
capture() 的參數跟 ChromaDB 預期的舊版不同，每次都在日誌留下一筆
「Failed to send telemetry event … capture() takes 1 positional argument but 3 were given」
（2026-10-31 到 11-10 累積 457 筆）。docker-compose 設的 ANONYMIZED_TELEMETRY=False 擋不住，
因為 ChromaDB 不論開關都會先呼叫 capture()。所以直接換成什麼都不做的實作。

同一個行程裡的 client 設定必須一致（ChromaDB 會檢查），測試與評測也都要從這裡建立
"""
import chromadb
from chromadb.config import Settings
from chromadb.telemetry.product import ProductTelemetryClient, ProductTelemetryEvent
from overrides import override


class NoTelemetry(ProductTelemetryClient):
    """不送任何使用統計"""

    # ChromaDB 的元件要求覆寫的方法加上 @override（overrides 套件，它自己的依賴）
    @override
    def capture(self, event: ProductTelemetryEvent) -> None:
        return None


_IMPL = f"{__name__}.NoTelemetry"
SETTINGS = Settings(anonymized_telemetry=False, chroma_product_telemetry_impl=_IMPL, chroma_telemetry_impl=_IMPL)


def make_client(path: str | None = None):
    """有 path 就落地到那個資料夾，沒有就是記憶體裡的 client"""
    if path:
        return chromadb.PersistentClient(path=path, settings=SETTINGS)
    return chromadb.Client(SETTINGS)
