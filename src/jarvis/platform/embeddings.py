import threading
from pathlib import Path

from fastembed import TextEmbedding


class FastEmbedTextEmbeddings:
    """CPU-only BGE-small embeddings with weights in JARVIS-managed storage."""

    model_name = "BAAI/bge-small-en-v1.5"
    dimensions = 384

    def __init__(self, *, cache_directory: Path, threads: int = 4) -> None:
        if threads < 1 or threads > 16:
            raise ValueError("embedding threads must be between 1 and 16")
        self._cache_directory = cache_directory.resolve()
        self._threads = threads
        self._lock = threading.RLock()
        self._model: TextEmbedding | None = None

    def embed_documents(self, texts: list[str]) -> list[tuple[float, ...]]:
        if len(texts) > 10_000:
            raise ValueError("embedding batch cannot exceed 10000 documents")
        if not texts:
            return []
        with self._lock:
            return [tuple(float(value) for value in vector) for vector in self._load().embed(texts)]

    def embed_query(self, text: str) -> tuple[float, ...]:
        if not text or len(text) > 32_000:
            raise ValueError("embedding query must contain between 1 and 32000 characters")
        with self._lock:
            vectors = self._load().query_embed(text)
            return tuple(float(value) for value in next(iter(vectors)))

    def _load(self) -> TextEmbedding:
        if self._model is None:
            self._cache_directory.mkdir(parents=True, exist_ok=True)
            self._model = TextEmbedding(
                model_name=self.model_name,
                cache_dir=str(self._cache_directory),
                threads=self._threads,
                cuda=False,
            )
        return self._model
