import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
import chromadb
import config
from services.embeddings import build_embedding_function
from services.metrics import metrics
from services.shelter_profile import profile, strip_region_tag
from services.query_rules import QueryPlan, MAX_FILTERED_RESULTS, MAX_RANKED_RESULTS

logger = logging.getLogger(__name__)

FINGERPRINT_FILE = "index_fingerprint.json"
# 重建索引時先建到這個暫存 collection，embedding 跑完才整個換過去
REBUILD_SUFFIX = "__rebuild"

# 拿來分隔文件，避免不同切分方式算出相同指紋
SEPARATOR = b"|--|"
# build_document 裡的「（別名：…）」段落
ALIAS_PATTERN = re.compile(r"（別名：[^）]*）")
# build_metadata 的欄位版本：新增或改動欄位時加一，落地的舊索引才會重建
METADATA_VERSION = 2


@dataclass
class Hit:
    name: str
    document: str
    distance: float
    metadata: dict


@dataclass
class PlannedResult:
    """規則層檢索的結果，附帶「有沒有用篩選、篩選有沒有落空、是不是容量排名、是不是點名」給 prompt 用"""
    hits: list
    # 符合條件的文件總數。hits 會被 MAX_FILTERED_RESULTS / MAX_RANKED_RESULTS 截斷，
    # 標頭要靠這個數字照實說「共幾筆、以下列幾筆」，不能讓模型把看到的筆數當成全部
    total: int = 0
    filtered: bool = False
    fell_back: bool = False
    ranked: bool = False
    named: bool = False


# 檢索結果的形狀：規則層篩選命中、篩選落空退回、容量排名、點名、無篩選的純語意，
# 以及被距離門檻擋掉。評測量的是這些路徑「應該」怎麼走，這裡量的是線上實際怎麼走
RETRIEVAL_COUNTERS = (
    "retrieval.filtered",
    "retrieval.fell_back",
    "retrieval.ranked",
    "retrieval.named",
    "retrieval.unfiltered",
    "retrieval.truncated",
    "retrieval.no_data",
    "retrieval.no_match",
)
metrics.register(*RETRIEVAL_COUNTERS)

# search() 在沒有東西可給模型時回的固定句子；chat_service 看到就直接回「沒有相關資料」，不必再打 LLM
NO_DATA = "目前沒有避難所資料。"
NO_MATCH = "找不到相關避難所資料。"


class VectorStore:
    def __init__(self, embedding_function=None, collection_name: str = "shelters", persist_path: str | None = None):
        # persist_path 留空 = 記憶體索引（測試、本機直跑）；有值就落地，重啟不必重新 embedding
        self.persist_path = config.CHROMA_PATH if persist_path is None else persist_path
        self.collection_name = collection_name
        self.ef = embedding_function or build_embedding_function()
        if self.persist_path:
            Path(self.persist_path).mkdir(parents=True, exist_ok=True)
            self.client = chromadb.PersistentClient(path=self.persist_path)
        else:
            self.client = chromadb.Client()
        # 讀寫 self.collection 都要拿這把鎖（RLock：search → plan_retrieve → retrieve 會巢狀進來）。
        # 重建索引時 embedding 在鎖外面跑，只有最後「換 collection」那一瞬間持鎖，查詢不會被擋幾十秒。
        self._lock = threading.RLock()
        # 兩個 /api/sync 同時進來時重建要排隊，不能同時操作同一個暫存 collection
        self._build_lock = threading.Lock()
        self._drop_collection(self._rebuild_name())
        self.collection = self._open_collection(collection_name)

    def _rebuild_name(self) -> str:
        return f"{self.collection_name}{REBUILD_SUFFIX}"

    def _open_collection(self, name: str):
        return self.client.get_or_create_collection(
            name=name,
            embedding_function=self.ef,
            metadata={"hnsw:space": "cosine"},
        )

    def _drop_collection(self, name: str) -> None:
        # 上次重建到一半被中斷會留下暫存 collection，開場先清掉
        try:
            self.client.delete_collection(name)
        except Exception:
            pass

    def _ef_name(self) -> str:
        return self.ef.name() if hasattr(self.ef, "name") else type(self.ef).__name__

    def _fingerprint_path(self) -> Path | None:
        return Path(self.persist_path) / FINGERPRINT_FILE if self.persist_path else None

    @staticmethod
    def doc_id(name: str) -> str:
        # 用名稱推導 id，局部更新時才找得到對應文件，不受資料庫回傳順序影響
        return "shelter_" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]

    def _fingerprint(self, documents: list[str]) -> str:
        # 換 embedding 模型也要重建，所以把模型名一起算進去；
        # 文件先排序，資料庫回傳順序不同不會被誤判成資料變了
        digest = hashlib.sha256(self._ef_name().encode("utf-8"))
        # metadata 欄位改了（例如新增 road）文件內容不會變，靠版本號讓落地的索引重建
        digest.update(f"metadata-v{METADATA_VERSION}".encode("utf-8"))
        for doc in sorted(documents):
            digest.update(SEPARATOR)
            digest.update(doc.encode("utf-8"))
        return digest.hexdigest()

    def _read_fingerprint(self) -> dict | None:
        path = self._fingerprint_path()
        if path is None or not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("VectorStore: 索引指紋讀取失敗，改為重建索引")
            return None

    def _write_fingerprint(self, fingerprint: str, count: int) -> None:
        path = self._fingerprint_path()
        if path is None:
            return
        try:
            path.write_text(
                json.dumps({"fingerprint": fingerprint, "count": count, "embedding": self._ef_name()}),
                encoding="utf-8",
            )
        except OSError:
            logger.warning("VectorStore: 索引指紋寫入失敗，下次啟動會重建索引")

    def count(self) -> int:
        with self._lock:
            return self.collection.count()

    #建立字串寫進 ChromaDB
    @staticmethod
    def build_document(s) -> str:
        clean_name = strip_region_tag(s.name)
        p = profile(s.name, s.address, s.capacity)
        alias_text = f"（別名：{'、'.join(p['aliases'])}）" if p["aliases"] else ""
        location = f"{p['county']}{p['township']}" if p["township"] else p["county"]
        address_text = f"，地址在{s.address}" if s.address else ""
        facility_text = p["facility"] if p["facility"] != "其他" else "公共設施"
        return (
            f"{clean_name}{alias_text}是{p['region']}地區的避難收容場所，"
            f"位於{location}{address_text}。"
            f"設施類型為{facility_text}。"
            f"容量 {s.capacity} 人，屬於{p['size_class']}避難所，"
            f"目前收容 {s.current_people} 人，尚有 {s.remaining} 個空位，"
            f"負載率 {s.occupancy_rate:.0f}%。"
        )

    #同樣的字串存成metadata
    @staticmethod
    def build_metadata(s) -> dict:
        p = profile(s.name, s.address, s.capacity)
        return {
            "name": s.name,
            "region": p["region"],
            "county": p["county"],
            "township": p["township"],
            "road": p["road"],
            "facility": p["facility"],
            "size_class": p["size_class"],
            "address": s.address,
            "lat": s.lat,
            "lon": s.lon,
            "capacity": s.capacity,
            "current_people": s.current_people,
            "remaining": s.remaining,
            "occupancy_rate": round(s.occupancy_rate, 1),
        }

    def build_index(self, shelters: list, force: bool = False) -> None:
        """
        將避難所資料向量化並存入 ChromaDB。
        索引已落地且內容未變時直接略過，避免每次重啟都重跑 embedding；
        force=True 用於手動同步，無論如何都重建。
        """
        if not shelters:
            logger.warning("VectorStore: 沒有資料可以建立索引")
            return

        documents = [self.build_document(s) for s in shelters]
        fingerprint = self._fingerprint(documents)
        saved = self._read_fingerprint()
        if (
            not force
            and saved is not None
            and saved.get("fingerprint") == fingerprint
            and self.count() == len(documents)
        ):
            logger.info("VectorStore: 索引內容未變（%d 筆），略過重建", len(documents))
            return

        # 先建到暫存 collection：embedding 要跑幾十秒，這段時間查詢仍然打舊索引，
        # 不會像「先刪光再加回」那樣出現一段回答「目前沒有避難所資料」的空窗
        with self._build_lock:
            rebuild_name = self._rebuild_name()
            self._drop_collection(rebuild_name)
            staging = self._open_collection(rebuild_name)
            try:
                staging.add(
                    documents=documents,
                    metadatas=[self.build_metadata(s) for s in shelters],
                    ids=[self.doc_id(s.name) for s in shelters],
                )
            except Exception:
                self._drop_collection(rebuild_name)
                raise

            with self._lock:
                self._drop_collection(self.collection_name)
                staging.modify(name=self.collection_name)
                self.collection = staging
            self._write_fingerprint(fingerprint, len(documents))
        logger.info("VectorStore: 成功建立 %d 筆避難所索引（embedding=%s）", len(documents), self._ef_name())

    def upsert_shelters(self, shelters: list) -> int:
        """
        只重算給定避難所的文件與向量（模擬回寫佔用數之後用）。
        整批重建要跑幾十次 embedding，這裡只碰有變動的幾筆，幾秒內就能讓 AI 看到新負載。
        """
        if not shelters:
            return 0
        documents = [self.build_document(s) for s in shelters]
        # embedding 要打 Ollama，持鎖做會把查詢與 readiness 的 count() 一起擋住
        embeddings = self.ef(documents)
        with self._lock:
            self.collection.upsert(
                documents=documents,
                embeddings=embeddings,
                metadatas=[self.build_metadata(s) for s in shelters],
                ids=[self.doc_id(s.name) for s in shelters],
            )
            # 指紋要跟著落地的內容走，否則下次啟動會被判定「資料變了」而整批重建
            stored = self.collection.get(include=["documents"])
        documents = stored.get("documents") or []
        self._write_fingerprint(self._fingerprint(documents), len(documents))
        logger.info("VectorStore: 局部更新 %d 筆避難所索引", len(shelters))
        return len(shelters)

    def count_where(self, where: dict | None) -> int:
        """符合 metadata 條件的文件數；沒有條件就是全部"""
        with self._lock:
            if where is None:
                return self.collection.count()
            return len(self.collection.get(where=where, include=[])["ids"])

    def embed_query(self, query: str):
        """
        算查詢向量。故意不持鎖：這一步要打 Ollama embedding，是整條檢索裡最慢的一段
        （最久 EMBEDDING_TIMEOUT 秒），包進鎖裡會連 count() 都排不進去。
        """
        vectors = self.ef([query])
        if vectors is None or len(vectors) == 0:
            raise RuntimeError("embedding 沒有回傳查詢向量")
        return vectors[0]

    def retrieve(self, query: str, n_results: int | None = None, where: dict | None = None) -> list[Hit]:
        return self.retrieve_with_total(query, n_results, where)[0]

    def retrieve_with_total(
        self, query: str, n_results: int | None = None, where: dict | None = None
    ) -> tuple[list[Hit], int]:
        """
        語意檢索，另外回傳符合條件的文件總數。給 where 時只在符合 metadata 條件的文件裡找，
        而且預設把符合的全部回傳（上限 MAX_FILTERED_RESULTS），
        「宜蘭有哪些避難所」這種列舉題才不會被 top-k 截掉；真的超過上限時 hits 會少於總數，
        標頭要照實講，不能讓模型把看到的筆數當成全部。

        鎖只包住真正讀索引的那兩段，查詢向量在鎖外面算：embedding 慢的時候
        連 readiness 走的 count() 都會被擋住，容器的 healthcheck 10 秒就判 unhealthy。
        """
        with self._lock:
            total = self.count_where(where)
        if total == 0:
            return [], 0

        default_n = MAX_FILTERED_RESULTS if where is not None else config.RAG_TOP_K
        n = min(n_results or default_n, total)
        query_embedding = self.embed_query(query)
        with self._lock:
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=n,
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        dists = results.get("distances", [[]])[0]
        hits = [
            Hit(name=meta["name"], document=doc, distance=float(dist), metadata=meta)
            for doc, meta, dist in zip(docs, metas, dists)
        ]
        return hits, total

    def rank_by_capacity(self, where: dict | None, descending: bool = True) -> list[Hit]:
        """
        「哪間最大 / 容量排名」不該靠語意 top-k 再排序，top-k 裡未必有真正最大的那間；
        這裡直接把符合條件的全部拿出來依 metadata 的容量排，結果才是精確的。
        回傳完整排序不截斷，要列幾筆、總共幾筆由呼叫端決定（標頭要用到總數）。
        """
        with self._lock:
            stored = self.collection.get(where=where, include=["documents", "metadatas"])
        docs = stored.get("documents") or []
        metas = stored.get("metadatas") or []
        hits = [Hit(name=meta["name"], document=doc, distance=0.0, metadata=meta) for doc, meta in zip(docs, metas)]
        hits.sort(key=lambda h: h.metadata.get("capacity", 0), reverse=descending)
        return hits

    def plan_retrieve(self, query: str, plan: QueryPlan, n_results: int | None = None) -> PlannedResult:
        """
        依查詢規則層的計畫檢索：有硬條件就先用 metadata 篩選，
        篩完沒東西再退回一般檢索（規則抽錯總比答不出來好），但要把「落空」記下來，
        prompt 才能告訴模型這些只是最接近的資料，不是符合條件的結果。
        """
        where = plan.to_where()
        descending = plan.capacity_order != "asc"
        if plan.order_by_capacity:
            ranked = self.rank_by_capacity(where, descending)
            fell_back = False
            if not ranked and where is not None:
                ranked = self.rank_by_capacity(None, descending)
                fell_back = True
            return PlannedResult(
                ranked[: (n_results or MAX_RANKED_RESULTS)],
                total=len(ranked),
                filtered=where is not None and not fell_back,
                fell_back=fell_back,
                ranked=True,
            )

        hits, total = self.retrieve_with_total(query, n_results, where=where) if where is not None else ([], 0)
        fell_back = False
        if not hits:
            fell_back = where is not None
            hits, total = self.retrieve_with_total(query, n_results)
        # 使用者直接點名某間避難所（「中正國小還有空位嗎」）：只留那幾筆。
        # 不然小模型會把 top-k 的十筆全部列出來，答非所問還會超過輸出長度
        named = [h for h in hits if strip_region_tag(h.name) in query]
        if named:
            return PlannedResult(
                named, total=len(named),
                filtered=where is not None and not fell_back, fell_back=fell_back, named=True,
            )
        return PlannedResult(
            hits, total=total,
            filtered=where is not None and not fell_back, fell_back=fell_back,
        )

    def retrieve_planned(self, query: str, plan: QueryPlan, n_results: int | None = None) -> list[Hit]:
        return self.plan_retrieve(query, plan, n_results).hits

    @staticmethod
    def _count_shape(result: PlannedResult, unfiltered: bool) -> None:
        """
        記下這一次檢索走哪條路。fell_back 代表規則層抽出的條件在索引裡沒有東西符合，
        送進 prompt 的只是「最接近的資料」——線上這個比例偏高就表示規則層抽錯了。
        """
        if result.named:
            metrics.incr("retrieval.named")
        elif result.fell_back:
            metrics.incr("retrieval.fell_back")
        elif result.filtered:
            metrics.incr("retrieval.filtered")
        elif result.ranked:
            metrics.incr("retrieval.ranked")
        if unfiltered:
            metrics.incr("retrieval.unfiltered")
        if result.total > len(result.hits):
            metrics.incr("retrieval.truncated")

    @staticmethod
    def _planned_header(plan: QueryPlan, result: PlannedResult) -> str | None:
        order = "由小到大" if plan.capacity_order == "asc" else "由大到小"
        condition = plan.describe()
        shown = len(result.hits)
        # 符合的筆數超過上限時只會送前幾筆進 prompt；標頭要同時講總數與列出的筆數，
        # 不然模型會照抄「共 30 筆」當成全部，而實際上可能有 80 間
        truncated = result.total > shown
        if result.named:
            return f"使用者詢問的避難所資料如下（共 {shown} 筆），請只回答這幾間："
        if result.fell_back:
            return (
                f"沒有找到符合「{condition}」條件的避難所。"
                "以下是最接近的其他資料，僅供參考；回答時請先明確告知使用者沒有完全符合條件的避難所。"
            )
        if result.filtered and result.ranked:
            if truncated:
                return (
                    f"符合「{condition}」的避難所共 {result.total} 筆，"
                    f"以下是依容量{order}排序的前 {shown} 筆："
                )
            return f"符合「{condition}」的避難所依容量{order}排序（共 {shown} 筆）："
        if result.filtered:
            if truncated:
                return f"符合「{condition}」的避難所共 {result.total} 筆，以下列出最相關的 {shown} 筆："
            return f"符合「{condition}」的避難所共 {shown} 筆："
        if result.ranked:
            return f"全東部避難所依容量{order}排序（前 {shown} 筆）："
        return None

    def search(self, query: str, n_results: int | None = None, plan: QueryPlan | None = None) -> str:
        """
        語意搜尋：找出與 query 最相關的避難所資料，回傳可直接塞進 prompt 的文字。
        有規則層計畫時在前面加一行說明篩選條件與結果狀態。
        """
        if self.count() == 0:
            metrics.incr("retrieval.no_data")
            return NO_DATA

        header = None
        unfiltered = True
        if plan is not None:
            result = self.plan_retrieve(query, plan, n_results)
            hits = result.hits
            header = self._planned_header(plan, result)
            unfiltered = not (result.filtered or result.fell_back or result.ranked or result.named)
            self._count_shape(result, unfiltered)
        else:
            hits = self.retrieve(query, n_results)
            metrics.incr("retrieval.unfiltered")
        if not hits:
            metrics.incr("retrieval.no_match")
            return NO_MATCH
        # 純語意檢索永遠會回 top-k，「今天天氣如何」也會撈到十筆避難所；
        # 最接近的一筆都離得很遠時就當成沒有相關資料，模型才不會拿不相干的資料硬答。
        # 有 metadata 篩選的路徑不套用：篩選命中本身就是相關的證據
        if unfiltered and hits[0].distance > config.RAG_MAX_DISTANCE:
            metrics.incr("retrieval.no_match")
            return NO_MATCH

        # 別名只是給 embedding 用的，餵給模型反而會被照抄成「宜蘭國小（宜蘭國民小學）」，所以從 prompt 文字裡拿掉
        body = "\n".join(f"- {ALIAS_PATTERN.sub('', h.document)}" for h in hits)
        return f"{header}\n{body}" if header else body
