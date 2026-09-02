import logging
from dataclasses import dataclass
import chromadb
import config
from services.embeddings import build_embedding_function
from services.shelter_profile import profile, strip_region_tag

logger = logging.getLogger(__name__)


@dataclass
class Hit:
    name: str
    document: str
    distance: float
    metadata: dict


class VectorStore:
    def __init__(self, embedding_function=None, collection_name: str = "shelters"):
        self.client = chromadb.Client()
        self.ef = embedding_function or build_embedding_function()
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=self.ef,
            metadata={"hnsw:space": "cosine"},
        )

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
        ef_name = self.ef.name() if hasattr(self.ef, "name") else type(self.ef).__name__
        logger.info("VectorStore: 成功建立 %d 筆避難所索引（embedding=%s）", len(shelters), ef_name)

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
