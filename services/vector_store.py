import re
import logging
from dataclasses import dataclass
import chromadb
from chromadb.utils import embedding_functions
import config

logger = logging.getLogger(__name__)

REGION_TAG_PATTERN = re.compile(r"^\[[A-Z]+\]\s*")
REGION_LABELS = {"YILAN": "宜蘭", "HUALIEN": "花蓮", "TAITUNG": "台東"}


def _region_of(name: str) -> str:
    m = re.match(r"^\[([A-Z]+)\]", name or "")
    return REGION_LABELS.get(m.group(1), "") if m else ""

@dataclass
class Hit:
    name: str
    document: str
    distance: float
    metadata: dict


def default_embedding_function():
    return embedding_functions.ONNXMiniLM_L6_V2()


class VectorStore:
    def __init__(self, embedding_function=None, collection_name: str = "shelters"):
        self.client = chromadb.Client()
        self.ef = embedding_function or default_embedding_function()
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=self.ef,
            metadata={"hnsw:space": "cosine"},
        )

    @staticmethod
    def build_document(s) -> str:
        clean_name = REGION_TAG_PATTERN.sub("", s.name)
        region = _region_of(s.name)
        return (
            f"{region}的{clean_name} 位於緯度 {s.lat}、經度 {s.lon}。"
            f"總容量為 {s.capacity} 人，"
            f"目前收容 {s.current_people} 人，"
            f"剩餘空間 {s.remaining} 人，"
            f"負載率 {s.occupancy_rate:.1f}%。"
        )

    @staticmethod
    def build_metadata(s) -> dict:
        return {
            "name": s.name,
            "region": _region_of(s.name),
            "lat": s.lat,
            "lon": s.lon,
            "capacity": s.capacity,
            "current_people": s.current_people,
            "remaining": s.remaining,
            "occupancy_rate": round(s.occupancy_rate, 1),
        }

    def build_index(self, shelters: list) -> None:
        """
        將避難所資料向量化並存入 ChromaDB
        """
        if not shelters:
            logger.warning("VectorStore: 沒有資料可以建立索引")
            return

        existing = self.collection.get()
        if existing["ids"]:
            self.collection.delete(ids=existing["ids"])

        self.collection.add(
            documents=[self.build_document(s) for s in shelters],
            metadatas=[self.build_metadata(s) for s in shelters],
            ids=[f"shelter_{i}" for i in range(len(shelters))],
        )
        logger.info("VectorStore: 成功建立 %d 筆避難所索引", len(shelters))

    def retrieve(self, query: str, n_results: int | None = None) -> list[Hit]:
        total = self.collection.count()
        if total == 0:
            return []

        n = min(n_results or config.RAG_TOP_K, total)
        results = self.collection.query(
            query_texts=[query],
            n_results=n,
            include=["documents", "metadatas", "distances"],
        )
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        dists = results.get("distances", [[]])[0]
        return [
            Hit(name=meta["name"], document=doc, distance=float(dist), metadata=meta)
            for doc, meta, dist in zip(docs, metas, dists)
        ]

    def search(self, query: str, n_results: int | None = None) -> str:
        """
        語意搜尋：找出與 query 最相關的避難所資料
        """
        if self.collection.count() == 0:
            return "目前沒有避難所資料。"

        hits = self.retrieve(query, n_results)
        if not hits:
            return "找不到相關避難所資料。"

        return "\n".join(f"- {h.document}" for h in hits)
