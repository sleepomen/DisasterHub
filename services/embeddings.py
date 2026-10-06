import logging

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
            json={"model": self.model, "input": texts},
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
    if provider == "minilm":
        from chromadb.utils import embedding_functions
        return embedding_functions.ONNXMiniLM_L6_V2()
    raise ValueError(f"未知的 EMBEDDING_PROVIDER：{provider}")
