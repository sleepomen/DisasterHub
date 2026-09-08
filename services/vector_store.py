import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
import chromadb
import config
from services.embeddings import build_embedding_function
from services.shelter_profile import profile, strip_region_tag
from services.query_rules import QueryPlan, MAX_FILTERED_RESULTS

logger = logging.getLogger(__name__)

FINGERPRINT_FILE = "index_fingerprint.json"

# 拿來分隔文件，避免不同切分方式算出相同指紋
SEPARATOR = b"|--|"


@dataclass
class Hit:
    name: str
    document: str
    distance: float
    metadata: dict


class VectorStore:
    def __init__(self, embedding_function=None, collection_name: str = "shelters", persist_path: str | None = None):
        # persist_path 留空 = 記憶體索引（測試、本機直跑）；有值就落地，重啟不必重新 embedding
        self.persist_path = config.CHROMA_PATH if persist_path is None else persist_path
        self.ef = embedding_function or build_embedding_function()
        if self.persist_path:
            Path(self.persist_path).mkdir(parents=True, exist_ok=True)
            self.client = chromadb.PersistentClient(path=self.persist_path)
        else:
            self.client = chromadb.Client()
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=self.ef,
            metadata={"hnsw:space": "cosine"},
        )

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
        return self.collection.count()

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

    @staticmethod
    def build_metadata(s) -> dict:
        p = profile(s.name, s.address, s.capacity)
        return {
            "name": s.name,
            "region": p["region"],
            "county": p["county"],
            "township": p["township"],
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
            and self.collection.count() == len(documents)
        ):
            logger.info("VectorStore: 索引內容未變（%d 筆），略過重建", len(documents))
            return

        existing = self.collection.get()
        if existing["ids"]:
            self.collection.delete(ids=existing["ids"])

        self.collection.add(
            documents=documents,
            metadatas=[self.build_metadata(s) for s in shelters],
            ids=[self.doc_id(s.name) for s in shelters],
        )
        self._write_fingerprint(fingerprint, len(documents))
        logger.info("VectorStore: 成功建立 %d 筆避難所索引（embedding=%s）", len(documents), self._ef_name())

    def upsert_shelters(self, shelters: list) -> int:
        """
        只重算給定避難所的文件與向量（模擬回寫佔用數之後用）。
        整批重建要跑幾十次 embedding，這裡只碰有變動的幾筆，幾秒內就能讓 AI 看到新負載。
        """
        if not shelters:
            return 0
        self.collection.upsert(
            documents=[self.build_document(s) for s in shelters],
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
        if where is None:
            return self.collection.count()
        return len(self.collection.get(where=where, include=[])["ids"])

    def retrieve(self, query: str, n_results: int | None = None, where: dict | None = None) -> list[Hit]:
        """
        語意檢索。給 where 時只在符合 metadata 條件的文件裡找，
        而且預設把符合的全部回傳（上限 MAX_FILTERED_RESULTS），
        「宜蘭有哪些避難所」這種列舉題才不會被 top-k 截掉。
        """
        total = self.count_where(where)
        if total == 0:
            return []

        default_n = MAX_FILTERED_RESULTS if where is not None else config.RAG_TOP_K
        n = min(n_results or default_n, total)
        results = self.collection.query(
            query_texts=[query],
            n_results=n,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        dists = results.get("distances", [[]])[0]
        return [
            Hit(name=meta["name"], document=doc, distance=float(dist), metadata=meta)
            for doc, meta, dist in zip(docs, metas, dists)
        ]

    def retrieve_planned(self, query: str, plan: QueryPlan, n_results: int | None = None) -> list[Hit]:
        """
        依查詢規則層的計畫檢索：有硬條件就先用 metadata 篩選，
        篩完沒東西再退回一般語意檢索（規則抽錯總比答不出來好）；
        「最大 / 最多」類問題再依容量重排。
        """
        where = plan.to_where()
        hits = self.retrieve(query, n_results, where=where) if where is not None else []
        if not hits:
            hits = self.retrieve(query, n_results)
        if plan.order_by_capacity:
            hits = sorted(hits, key=lambda h: h.metadata.get("capacity", 0), reverse=True)
        return hits

    def search(self, query: str, n_results: int | None = None, plan: QueryPlan | None = None) -> str:
        """
        語意搜尋：找出與 query 最相關的避難所資料，回傳可直接塞進 prompt 的文字
        """
        if self.collection.count() == 0:
            return "目前沒有避難所資料。"

        hits = self.retrieve_planned(query, plan, n_results) if plan is not None else self.retrieve(query, n_results)
        if not hits:
            return "找不到相關避難所資料。"

        return "\n".join(f"- {h.document}" for h in hits)
