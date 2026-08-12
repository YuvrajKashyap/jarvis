import asyncio
import re
from contextlib import suppress

from pydantic import BaseModel, ConfigDict, Field

from jarvis.memory.history import ConversationHistory, ConversationMessage, ConversationRole
from jarvis.memory.store import MemoryCandidate, MemoryRepository


class ConsolidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    messages_processed: int = Field(ge=0)
    candidates_created: int = Field(ge=0)
    conflicts_created: int = Field(ge=0)


class ConversationConsolidator:
    """Extracts only high-confidence explicit user facts into canonical memory."""

    def __init__(
        self,
        *,
        history: ConversationHistory,
        memory: MemoryRepository,
        batch_size: int = 100,
        interval_seconds: float = 30,
    ) -> None:
        if batch_size < 1 or batch_size > 500:
            raise ValueError("consolidation batch size must be between 1 and 500")
        self._history = history
        self._memory = memory
        self._batch_size = batch_size
        if interval_seconds < 1 or interval_seconds > 3_600:
            raise ValueError("consolidation interval must be between 1 and 3600 seconds")
        self._interval_seconds = interval_seconds
        self._worker: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run(), name="jarvis-memory-consolidation")

    async def stop(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker

    async def _run(self) -> None:
        while True:
            with suppress(OSError, RuntimeError, ValueError):
                await self.run_once()
            await asyncio.sleep(self._interval_seconds)

    async def run_once(self) -> ConsolidationResult:
        messages = await asyncio.to_thread(
            self._history.unconsolidated,
            limit=self._batch_size,
        )
        created = 0
        conflicts = 0
        for message in messages:
            for candidate in _explicit_candidates(message):
                mutation = await asyncio.to_thread(self._memory.remember, candidate)
                if mutation.kind == "created":
                    created += 1
                elif mutation.kind == "conflict":
                    conflicts += 1
        if messages:
            await asyncio.to_thread(
                self._history.mark_consolidated,
                tuple(message.message_id for message in messages),
            )
        return ConsolidationResult(
            messages_processed=len(messages),
            candidates_created=created,
            conflicts_created=conflicts,
        )


def _explicit_candidates(message: ConversationMessage) -> tuple[MemoryCandidate, ...]:
    if message.role is not ConversationRole.USER:
        return ()
    candidates: list[MemoryCandidate] = []
    phone = re.search(
        r"\bI (?:have|use|own) (?:an? )?(iPhone [A-Za-z0-9 ]+?)(?:[,.]|\band\b|$)",
        message.content,
        re.I,
    )
    if phone:
        model = " ".join(phone.group(1).split())
        candidates.append(
            _candidate(
                message,
                category="device",
                subject="phone",
                content=f"Yuvraj has an {model}.",
            )
        )
    cost_preference = re.search(
        r"\b(?:prefer|want|need)\b.*\bJARVIS\b.*\blocal\b.*\bfree\b",
        message.content,
        re.I,
    )
    if cost_preference:
        candidates.append(
            _candidate(
                message,
                category="preference",
                subject="JARVIS operating cost",
                content="Yuvraj prefers JARVIS to remain local and free to run.",
            )
        )
    identity = re.search(
        r"\b(?:my name is|I'm called)\s+([A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+){0,3})[.!?]?",
        message.content,
        re.I,
    )
    if identity:
        name = _clean_clause(identity.group(1))
        candidates.append(
            _candidate(
                message,
                category="identity",
                subject="name",
                content=f"Yuvraj's name is {name}.",
            )
        )
    person = re.search(
        r"\b([A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+){0,2}) is my "
        r"(brother|sister|mother|father|parent|friend|coach|teammate|roommate|partner)[.!?]?",
        message.content,
        re.I,
    )
    if person:
        name = _clean_clause(person.group(1))
        relationship = person.group(2).casefold()
        candidates.append(
            _candidate(
                message,
                category="person",
                subject=name,
                content=f"{name} is Yuvraj's {relationship}.",
            )
        )
    project = re.search(
        r"\b(?:I am|I'm) working on\s+(.+?)(?:[.!?]|$)",
        message.content,
        re.I,
    )
    if project:
        name = _clean_clause(project.group(1))
        candidates.append(
            _candidate(
                message,
                category="project",
                subject=name,
                content=f"Yuvraj is working on {name}.",
            )
        )
    decision = re.search(
        r"\b(?:we|I) (?:have )?decided (?:that |to )?(.+?)(?:[.!?]|$)",
        message.content,
        re.I,
    )
    if decision:
        choice = _clean_clause(decision.group(1))
        candidates.append(
            _candidate(
                message,
                category="decision",
                subject=choice,
                content=f"Yuvraj decided to {choice}.",
            )
        )
    unfinished = re.search(
        r"\bI (?:still need to|need to|have to)\s+(.+?)(?:[.!?]|$)",
        message.content,
        re.I,
    )
    if unfinished:
        task = _clean_clause(unfinished.group(1))
        candidates.append(
            _candidate(
                message,
                category="unfinished_work",
                subject=task,
                content=f"Yuvraj still needs to {task}.",
            )
        )
    preference = re.search(
        r"\bI (?:prefer|like)\s+(.+?)(?:[.!?]|$)",
        message.content,
        re.I,
    )
    if preference and cost_preference is None:
        preference_text = _clean_clause(preference.group(1))
        candidates.append(
            _candidate(
                message,
                category="preference",
                subject=preference_text,
                content=f"Yuvraj prefers {preference_text}.",
            )
        )
    return tuple(candidates)


def _clean_clause(value: str) -> str:
    return " ".join(value.strip(" \t\r\n.,!?;:").split())[:240]


def _candidate(
    message: ConversationMessage,
    *,
    category: str,
    subject: str,
    content: str,
) -> MemoryCandidate:
    return MemoryCandidate(
        category=category,
        subject=subject,
        content=content,
        source_event_ids=(message.source_event_id,),
        observed_at=message.created_at,
    )
