"""
防災知識文件（data_reference/knowledge/*.md）：解析、建索引、檢索。

避難所索引回答「去哪裡」，這裡回答「怎麼做、警戒是什麼意思」。兩者放在不同的 collection：
混在一起的話，34 段知識文件要跟 644 筆避難所搶 top-k，而且避難所那邊的 metadata 篩選、
概況、點名都是依避難所欄位設計的，知識文件套不上
"""
import hashlib
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import chromadb

import config
from services.embeddings import build_embedding_function
from services.metrics import metrics

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "data_reference" / "knowledge"
COLLECTION_NAME = "knowledge"

HAZARDS = ("typhoon", "earthquake", "debris_flow", "tsunami", "flood")
KINDS = ("alert", "about", "before", "during", "after")
# 回答時提到的災害名稱
HAZARD_NAMES = {
    "typhoon": "颱風", "earthquake": "地震", "debris_flow": "土石流", "tsunami": "海嘯", "flood": "豪雨與淹水",
}

_HEADING = re.compile(r"^## (?P<id>[a-z0-9-]+)｜(?P<title>.+)$")
_FIELD = re.compile(r"^- (?P<key>[a-z_]+): (?P<value>.+)$")

# 每題最多回幾段。原文直接給使用者看，一段 100～300 字，兩段已經是一整個畫面
MAX_CHUNKS = 2
# 第二段以後跟第一段的距離差超過這個值就不送：「颱風警報什麼時候解除」只該拿到警報那兩段，
# 不需要再附一段颱風過後的清潔事項。原本 0.08，實測「地震的時候在家裡要怎麼辦」
# 室內 0.213、戶外 0.261、房屋受損 0.263 三段全送，回答就混進了學校與公共場所的做法
FOLLOWER_MARGIN = 0.05

metrics.register("knowledge.hit", "knowledge.no_match")


@dataclass
class KnowledgeChunk:
    id: str
    title: str
    hazard: str
    kind: str
    text: str
    retrieved: str
    # [(來源名稱, 網址)]；一段可以有兩個來源
    sources: list[tuple[str, str]] = field(default_factory=list)

    @property
    def document(self) -> str:
        # 標題本身就是一個問句（「土石流黃色警戒、紅色警戒是什麼意思」），一起 embed 對口語問題比較好命中
        return f"{self.title}\n{self.text}"


@dataclass
class KnowledgeHit:
    chunk: KnowledgeChunk
    distance: float


class KnowledgeFormatError(ValueError):
    pass


def parse_file(text: str, filename: str = "") -> list[KnowledgeChunk]:
    """
    一個 Markdown 檔 → 段落。格式見 data_reference/knowledge/README.md。
    格式錯誤直接拋例外：少了來源或災害欄位的段落不能悄悄進索引
    """
    chunks = []
    current = None
    body: list[str] = []
    pending_source: str | None = None

    def close():
        if current is None:
            return
        current.text = "\n".join(line for line in body if line.strip()).strip()
        missing = [k for k in ("hazard", "kind", "retrieved") if not getattr(current, k)]
        if missing or not current.sources or not current.text:
            raise KnowledgeFormatError(f"{filename} {current.id}：缺少 {missing or '來源或正文'}")
        if current.hazard not in HAZARDS or current.kind not in KINDS:
            raise KnowledgeFormatError(f"{filename} {current.id}：hazard / kind 不在允許的值裡")
        chunks.append(current)

    for line in text.splitlines():
        heading = _HEADING.match(line)
        if heading:
            close()
            current = KnowledgeChunk(heading["id"], heading["title"].strip(), "", "", "", "")
            body, pending_source = [], None
            continue
        if current is None:
            continue
        meta = _FIELD.match(line)
        if meta and not body:
            key, value = meta["key"], meta["value"].strip()
            if key == "source":
                pending_source = value
            elif key == "url":
                # 一個來源可以有多個網址（同一篇文章分兩頁），每個網址都配上最近的來源名稱
                current.sources.append((pending_source or "", value))
            elif key in ("hazard", "kind", "retrieved"):
                setattr(current, key, value)
            continue
        body.append(line)
    close()
    return chunks


def load_chunks(directory: Path = KNOWLEDGE_DIR) -> list[KnowledgeChunk]:
    chunks = []
    for path in sorted(directory.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        chunks += parse_file(path.read_text(encoding="utf-8"), path.name)
    ids = [c.id for c in chunks]
    duplicated = {i for i in ids if ids.count(i) > 1}
    if duplicated:
        raise KnowledgeFormatError(f"段落 id 重複：{sorted(duplicated)}")
    return chunks


def sources_text(chunks: list[KnowledgeChunk]) -> str:
    """
    回答最後附的出處：由程式產生，不交給模型（模型會把網址抄錯，系統提示也不准它寫英文）。
    同一個網址只列一次（《颱風百問》第 61 題、第 88 題是同一份 PDF），同一個來源的多個網址併成一行
    """
    by_name: dict[str, list[str]] = {}
    seen_urls = set()
    for chunk in chunks:
        for name, url in chunk.sources:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            by_name.setdefault(name, []).append(url)
    return "資料來源：\n" + "\n".join(f"- {name} {' '.join(urls)}" for name, urls in by_name.items())


class KnowledgeStore:
    def __init__(self, embedding_function=None, client=None, directory: Path = KNOWLEDGE_DIR):
        self.ef = embedding_function or build_embedding_function()
        if client is None:
            client = chromadb.PersistentClient(path=config.CHROMA_PATH) if config.CHROMA_PATH else chromadb.Client()
        self.client = client
        self.directory = directory
        self._lock = threading.RLock()
        self.chunks: dict[str, KnowledgeChunk] = {}
        self.collection = None

    def _fingerprint(self, chunks: list[KnowledgeChunk]) -> str:
        name = self.ef.name() if hasattr(self.ef, "name") else type(self.ef).__name__
        digest = hashlib.sha256(name.encode("utf-8"))
        for chunk in chunks:
            digest.update(f"|{chunk.id}|{chunk.hazard}|{chunk.kind}|{chunk.document}".encode())
        return digest.hexdigest()

    def build(self) -> int:
        """
        讀文件、建索引。文件與 embedding 模型都沒變就沿用落地的 collection；
        指紋存在 collection 自己的 metadata 裡，不必另外寫檔
        """
        chunks = load_chunks(self.directory)
        fingerprint = self._fingerprint(chunks)
        with self._lock:
            existing = self.client.get_or_create_collection(
                name=COLLECTION_NAME, embedding_function=self.ef, metadata={"hnsw:space": "cosine"},
            )
            if (existing.metadata or {}).get("fingerprint") == fingerprint and existing.count() == len(chunks):
                self.collection = existing
                self.chunks = {c.id: c for c in chunks}
                logger.info("KnowledgeStore: 知識文件未變（%d 段），略過重建", len(chunks))
                return len(chunks)
            self.client.delete_collection(COLLECTION_NAME)
            collection = self.client.create_collection(
                name=COLLECTION_NAME,
                embedding_function=self.ef,
                metadata={"hnsw:space": "cosine", "fingerprint": fingerprint},
            )
            collection.add(
                ids=[c.id for c in chunks],
                documents=[c.document for c in chunks],
                metadatas=[{"hazard": c.hazard, "kind": c.kind} for c in chunks],
            )
            self.collection = collection
            self.chunks = {c.id: c for c in chunks}
        logger.info("KnowledgeStore: 建立 %d 段防災知識索引", len(chunks))
        return len(chunks)

    def ready(self) -> bool:
        return self.collection is not None and bool(self.chunks)

    def search(self, query: str, hazards: list[str] | None = None, n: int = MAX_CHUNKS) -> list[KnowledgeHit]:
        """
        問句提到災害時只在那些災害的段落裡找：「颱風前要準備什麼」不該拿到地震前的準備。
        結果依距離排序，第一段之後只留跟第一段差不多近的
        """
        if not self.ready():
            return []
        where = None
        if hazards:
            where = {"hazard": hazards[0]} if len(hazards) == 1 else {"hazard": {"$in": list(hazards)}}
        vector = self.ef([query])[0]
        with self._lock:
            result = self.collection.query(
                query_embeddings=[vector], n_results=min(n, len(self.chunks)), where=where, include=["distances"],
            )
        hits = [
            KnowledgeHit(self.chunks[cid], float(dist))
            for cid, dist in zip(result["ids"][0], result["distances"][0], strict=True)
            if cid in self.chunks
        ]
        if not hits:
            return []
        best = hits[0].distance
        return [h for h in hits if h.distance - best <= FOLLOWER_MARGIN]
