from uuid import UUID

from jarvis.memory.semantic import SemanticMemoryIndex
from jarvis.memory.store import MemoryFact, MemoryRepository


class HybridMemoryRetriever:
    """Combines exact FTS evidence with local semantic recall using reciprocal rank fusion."""

    def __init__(self, *, memory: MemoryRepository, semantic: SemanticMemoryIndex) -> None:
        self._memory = memory
        self._semantic = semantic

    def search(self, query: str, *, limit: int = 12) -> list[MemoryFact]:
        if limit < 1 or limit > 100:
            raise ValueError("retrieval limit must be between 1 and 100")
        lexical = self._memory.search(query, limit=min(limit * 2, 100))
        facts = self._memory.list_all()
        by_id = {fact.fact_id: fact for fact in facts}
        try:
            semantic_ids = self._semantic.search(
                f"query: {query}",
                facts=facts,
                limit=min(limit * 2, 100),
            )
        except (OSError, RuntimeError, ValueError):
            semantic_ids = []
        scores: dict[UUID, float] = {}
        for rank, fact in enumerate(lexical, start=1):
            scores[fact.fact_id] = scores.get(fact.fact_id, 0) + 1 / (60 + rank)
            by_id[fact.fact_id] = fact
        for rank, fact_id in enumerate(semantic_ids, start=1):
            scores[fact_id] = scores.get(fact_id, 0) + 1 / (60 + rank)
        ordered = sorted(scores, key=scores.__getitem__, reverse=True)
        return [by_id[fact_id] for fact_id in ordered[:limit] if fact_id in by_id]
