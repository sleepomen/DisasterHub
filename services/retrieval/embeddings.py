import logging
import math
import zlib

import requests

import config

logger = logging.getLogger(__name__)


class OllamaEmbedding:
    def __init__(self, model: str, host: str, timeout: int = 120, batch_size: int = 32):
        self.model = model
        self.url = f"{host}/api/embed"
        self.timeout = timeout
        self.batch_size = batch_size

    def name(self) -> str:
        return f"ollama-{self.model}"

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        response = requests.post(
            self.url,
            json={"model": self.model, "input": texts, "keep_alive": config.OLLAMA_KEEP_ALIVE},
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        embeddings = data.get("embeddings")
        if not embeddings or len(embeddings) != len(texts):
            raise RuntimeError(f"Ollama embed 回傳數量不符：{len(embeddings or [])} != {len(texts)}")
        return embeddings

    def __call__(self, input):
        texts = input if isinstance(input, list) else [input]
        out = []
        for i in range(0, len(texts), self.batch_size):
            out.extend(self._embed_batch(texts[i:i + self.batch_size]))
        return out

    def embed_query(self, input):
        return self(input)

    def embed_documents(self, input):
        return self(input)

    def warm_up(self) -> bool:
        try:
            self._embed_batch(["暖機"])
            return True
        except Exception:
            logger.exception("Ollama embedding 暖機失敗（model=%s, url=%s）", self.model, self.url)
            return False


class LexicalEmbedding:
    """
    不用模型的字面向量：問句與文件拆成單字與相鄰兩字，雜湊到固定維度，再做 L2 正規化，
    cosine 相似度就是「共同的字與詞有多少」。給輕量模式用（docker-compose.lite.yml）：
    不需要 Ollama、不必下載任何模型，在一般筆電上建 644 間的索引不到一秒。
    代價是不懂語意：「淡江蘭陽校區」對得到「淡江大學蘭陽校園」，「適合老人的」就對不到「避難弱者」。
    跟 bge-m3 的檢索比較見 docs/evaluation.md
    """

    DIM = 4096
    # 兩字詞比單字有鑑別力（「國小」對「國」「小」），權重給高一點
    UNIGRAM_WEIGHT = 0.5

    def name(self) -> str:
        return "lexical-v1"

    @classmethod
    def _tokens(cls, text: str) -> list[tuple[str, float]]:
        chars = [c for c in text.replace("臺", "台").lower() if c.isalnum()]
        grams = [(c, cls.UNIGRAM_WEIGHT) for c in chars]
        grams += [(a + b, 1.0) for a, b in zip(chars, chars[1:], strict=False)]
        return grams

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.DIM
        for gram, weight in self._tokens(text):
            vector[zlib.crc32(gram.encode("utf-8")) % self.DIM] += weight
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            # 全是標點的問句：給一個固定方向，cosine 才算得出來（跟每份文件都一樣遠）
            vector[0], norm = 1.0, 1.0
        return [v / norm for v in vector]

    def __call__(self, input):
        texts = input if isinstance(input, list) else [input]
        return [self._embed(t) for t in texts]

    def embed_query(self, input):
        return self(input)

    def embed_documents(self, input):
        return self(input)


def build_embedding_function(provider: str | None = None, model: str | None = None):
    provider = (provider or config.EMBEDDING_PROVIDER).lower()
    if provider == "ollama":
        ef = OllamaEmbedding(
            model=model or config.EMBEDDING_MODEL,
            host=config.OLLAMA_HOST,
            timeout=config.EMBEDDING_TIMEOUT,
        )
        ef.warm_up()
        return ef
    if provider == "lexical":
        return LexicalEmbedding()
    if provider == "minilm":
        from chromadb.utils import embedding_functions
        return embedding_functions.ONNXMiniLM_L6_V2()
    raise ValueError(f"未知的 EMBEDDING_PROVIDER：{provider}")
