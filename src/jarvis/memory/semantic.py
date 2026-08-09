import hashlib
import math
from array import array
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import text

from jarvis.memory.store import MemoryFact
from jarvis.platform.sqlite import SQLiteStore


class TextEmbeddings(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[tuple[float, ...]]: ...

    def embed_query(self, text: str) -> tuple[float, ...]: ...


class SemanticMemoryIndex:
    """Maintains a rebuildable local embedding index in canonical SQLite storage."""

    def __init__(self, *, sqlite: SQLiteStore, embeddings: TextEmbeddings) -> None:
        self._sqlite = sqlite
        self._embeddings = embeddings

    def search(
        self,
        query: str,
        *,
        facts: list[MemoryFact],
        limit: int,
    ) -> list[UUID]:
        if limit < 1 or limit > 100:
            raise ValueError("semantic search limit must be between 1 and 100")
        self._synchronize(facts)
        query_vector = self._validate_vector(self._embeddings.embed_query(query))
        with self._sqlite.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT fact_id, vector FROM memory_embedding WHERE model = :model LIMIT 10000"
                ),
                {"model": self._embeddings.model_name},
            ).all()
        scored = [
            (UUID(row.fact_id), _cosine(query_vector, _unpack_vector(row.vector))) for row in rows
        ]
        scored.sort(key=lambda item: item[1], reverse=True)
        return [fact_id for fact_id, score in scored if score > 0][:limit]

    def _synchronize(self, facts: list[MemoryFact]) -> None:
        if len(facts) > 10_000:
            raise ValueError("semantic index supports at most 10000 memory facts")
        prepared = {
            str(fact.fact_id): (
                _content_hash(fact),
                f"passage: {fact.category}\n{fact.subject}\n{fact.content}",
            )
            for fact in facts
        }
        with self._sqlite.engine.connect() as connection:
            existing = {
                row.fact_id: row.content_hash
                for row in connection.execute(
                    text(
                        "SELECT fact_id, content_hash FROM memory_embedding "
                        "WHERE model = :model LIMIT 10000"
                    ),
                    {"model": self._embeddings.model_name},
                )
            }
        changed = [
            (fact_id, content_hash, passage)
            for fact_id, (content_hash, passage) in prepared.items()
            if existing.get(fact_id) != content_hash
        ]
        vectors = self._embeddings.embed_documents([item[2] for item in changed])
        if len(vectors) != len(changed):
            raise RuntimeError("embedding provider returned an unexpected vector count")
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with self._sqlite._write_lock, self._sqlite.engine.begin() as connection:
            stale = set(existing) - set(prepared)
            for fact_id in stale:
                connection.execute(
                    text("DELETE FROM memory_embedding WHERE fact_id = :fact_id"),
                    {"fact_id": fact_id},
                )
            for (fact_id, content_hash, _passage), vector in zip(changed, vectors, strict=True):
                checked = self._validate_vector(vector)
                connection.execute(
                    text(
                        "INSERT INTO memory_embedding"
                        "(fact_id, model, content_hash, dimensions, vector, updated_at) "
                        "VALUES (:fact_id, :model, :content_hash, :dimensions, "
                        ":vector, :updated_at) "
                        "ON CONFLICT(fact_id) DO UPDATE SET "
                        "model=excluded.model, content_hash=excluded.content_hash, "
                        "dimensions=excluded.dimensions, vector=excluded.vector, "
                        "updated_at=excluded.updated_at"
                    ),
                    {
                        "fact_id": fact_id,
                        "model": self._embeddings.model_name,
                        "content_hash": content_hash,
                        "dimensions": self._embeddings.dimensions,
                        "vector": _pack_vector(checked),
                        "updated_at": now,
                    },
                )

    def _validate_vector(self, vector: tuple[float, ...]) -> tuple[float, ...]:
        if len(vector) != self._embeddings.dimensions:
            raise RuntimeError("embedding vector has the wrong dimensions")
        if not all(math.isfinite(value) for value in vector):
            raise RuntimeError("embedding vector contains a non-finite value")
        return vector


def _content_hash(fact: MemoryFact) -> str:
    content = f"{fact.category}\0{fact.subject}\0{fact.content}".encode()
    return hashlib.sha256(content).hexdigest()


def _pack_vector(vector: tuple[float, ...]) -> bytes:
    values = array("f", vector)
    return values.tobytes()


def _unpack_vector(value: bytes) -> tuple[float, ...]:
    values = array("f")
    values.frombytes(value)
    return tuple(values)


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        return 0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)
