import re
import chromadb
from chromadb.utils import embedding_functions
import config

# 以下是新增的
REGION_TAG_PATTERN = re.compile(r"^\[[A-Z]+\]\s*")
REGION_LABELS = {"YILAN": "宜蘭", "HUALIEN": "花蓮", "TAITUNG": "台東"}


def _region_of(name: str) -> str:
    m = re.match(r"^\[([A-Z]+)\]", name or "")
    return REGION_LABELS.get(m.group(1), "") if m else ""

class VectorStore:
    def __init__(self):
        self.client = chromadb.Client()

        # 指定輕量 embedding model
        self.ef = embedding_functions.ONNXMiniLM_L6_V2()

        self.collection = self.client.get_or_create_collection(
            name="shelters",
            embedding_function=self.ef
        )

    def build_index(self, shelters: list) -> None:
        """
        將避難所資料向量化並存入 ChromaDB
        """
        if not shelters:
            print("VectorStore: 沒有資料可以建立索引")
            return

        existing = self.collection.get()
        if existing["ids"]:
            self.collection.delete(ids=existing["ids"])

        documents = []
        metadatas = []
        ids = []

        for i, s in enumerate(shelters):
            occupancy_rate = s.occupancy_rate if hasattr(s, 'occupancy_rate') else 0.0
            remaining = s.remaining

            clean_name = REGION_TAG_PATTERN.sub("", s.name)
            region = _region_of(s.name)
            doc = (
                f"{region}的{clean_name} 位於緯度 {s.lat}、經度 {s.lon}。"
                f"總容量為 {s.capacity} 人，"
                f"目前收容 {s.current_people} 人，"
                f"剩餘空間 {remaining} 人，"
                f"負載率 {occupancy_rate:.1f}%。"
            )

            documents.append(doc)
            metadatas.append({
                "name": s.name,
                "region": region,
                "lat": s.lat,
                "lon": s.lon,   
                "capacity": s.capacity,
                "current_people": s.current_people,
                "remaining": remaining,
                "occupancy_rate": round(occupancy_rate, 1)
            })
            ids.append(f"shelter_{i}")

        self.collection.add(
            documents=documents,
            metadatas=metadatas,
            ids=ids
        )
        print(f"VectorStore: 成功建立 {len(shelters)} 筆避難所索引")

    def search(self, query: str, n_results: int | None = None) -> str:
        """
        語意搜尋：找出與 query 最相關的避難所資料
        """
        total = self.collection.count()
        if total == 0:
            return "目前沒有避難所資料。"

        n = min(n_results or config.RAG_TOP_K, total)

        results = self.collection.query(
            query_texts=[query],
            n_results=n
        )

        docs = results.get("documents", [[]])[0]
        if not docs:
            return "找不到相關避難所資料。"

        return "\n".join(f"- {doc}" for doc in docs)