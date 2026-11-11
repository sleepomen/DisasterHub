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


# 每個 collection 的設定。ChromaDB 用近似最近鄰（HNSW），預設搜尋寬度 search_ef=10：
# 要前 10 筆時候選也只看 10 個，真正最近的那筆可能不在裡面。實測「外役監」最近的是光復自強外役監獄
# （距離 0.50，第二名 0.58），前 10 名裡卻沒有它，要 50 筆才排第一。
# 避難所 644 筆、知識 34 段，搜尋寬度開到比文件數大就等於精確搜尋，多花的時間量不出來。
# 改這裡會讓兩個索引的指紋變動、自動重建（設定只在建立 collection 時生效）
COLLECTION_METADATA = {"hnsw:space": "cosine", "hnsw:search_ef": 1000, "hnsw:construction_ef": 200}


def settings_tag() -> bytes:
    """算進索引指紋的設定字串：設定改了，落地的舊索引就重建"""
    return repr(sorted(COLLECTION_METADATA.items())).encode()


_IMPL = f"{__name__}.NoTelemetry"
SETTINGS = Settings(anonymized_telemetry=False, chroma_product_telemetry_impl=_IMPL, chroma_telemetry_impl=_IMPL)


def make_client(path: str | None = None):
    """有 path 就落地到那個資料夾，沒有就是記憶體裡的 client"""
    if path:
        return chromadb.PersistentClient(path=path, settings=SETTINGS)
    return chromadb.Client(SETTINGS)
