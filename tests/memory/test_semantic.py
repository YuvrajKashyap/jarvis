from datetime import UTC, datetime
from uuid import UUID

from jarvis.memory.retrieval import HybridMemoryRetriever
from jarvis.memory.semantic import SemanticMemoryIndex
from jarvis.memory.store import MemoryCandidate, MemoryRepository
from jarvis.platform.sqlite import SQLiteStore

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
SOURCE_ID = UUID("019fd977-1d96-7892-950c-6afbb71f7cf0")


class MeaningEmbedding:
    model_name = "test/meaning-v1"
    dimensions = 2

    def embed_documents(self, texts: list[str]) -> list[tuple[float, ...]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> tuple[float, ...]:
        lowered = text.casefold()
        if any(term in lowered for term in ("iphone", "phone", "mobile", "device")):
            return (1.0, 0.0)
        return (0.0, 1.0)


def test_hybrid_retrieval_finds_semantic_match_and_removes_stale_vectors(tmp_path) -> None:
    sqlite = SQLiteStore(tmp_path / "jarvis.db")
    sqlite.initialize()
    memory = MemoryRepository(sqlite=sqlite, markdown_directory=tmp_path / "Memory")
    memory.initialize()
    phone = memory.remember(
        MemoryCandidate(
            category="hardware",
            subject="iPhone",
            content="Yuvraj uses an iPhone 17 Pro.",
            source_event_ids=(SOURCE_ID,),
            observed_at=NOW,
        )
    )
    memory.remember(
        MemoryCandidate(
            category="sport",
            subject="tennis",
            content="Yuvraj plays NCAA Division II Tennis.",
            source_event_ids=(UUID("019fd977-1d96-7892-950c-6afbb71f7cf1"),),
            observed_at=NOW,
        )
    )
    index = SemanticMemoryIndex(sqlite=sqlite, embeddings=MeaningEmbedding())
    retriever = HybridMemoryRetriever(memory=memory, semantic=index)

    matches = retriever.search("What mobile does he carry?", limit=1)

    assert [fact.fact_id for fact in matches] == [phone.fact_id]

    memory.forget(phone.fact_id, forgotten_at=NOW)

    assert retriever.search("mobile device", limit=2) == []
