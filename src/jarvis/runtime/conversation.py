from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import UUID


class RuntimePhase(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    AWAITING_APPROVAL = "awaiting_approval"
    ACTING = "acting"
    SPEAKING = "speaking"


class ListeningMode(StrEnum):
    NORMAL = "normal"
    PRIVATE = "private"
    MEETING = "meeting"


class InvalidTransition(RuntimeError):
    pass


class DeviceOwnershipError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeSnapshot:
    phase: RuntimePhase = RuntimePhase.IDLE
    mode: ListeningMode = ListeningMode.NORMAL
    session_id: UUID | None = None
    turn_id: UUID | None = None
    active_device_id: str | None = None
    cancellation_generation: int = 0


class RuntimeCoordinator:
    def __init__(self) -> None:
        self._snapshot = RuntimeSnapshot()

    def snapshot(self) -> RuntimeSnapshot:
        return self._snapshot

    def activate(self, *, session_id: UUID, turn_id: UUID, device_id: str) -> RuntimeSnapshot:
        if not device_id:
            raise ValueError("device_id cannot be empty")
        cancellation_generation = self._snapshot.cancellation_generation
        if self._snapshot.phase is not RuntimePhase.IDLE:
            cancellation_generation += 1
        self._snapshot = RuntimeSnapshot(
            phase=RuntimePhase.LISTENING,
            mode=self._snapshot.mode,
            session_id=session_id,
            turn_id=turn_id,
            active_device_id=device_id,
            cancellation_generation=cancellation_generation,
        )
        return self._snapshot

    def submit_input(self, *, device_id: str, text: str) -> RuntimeSnapshot:
        if self._snapshot.phase is not RuntimePhase.LISTENING:
            raise InvalidTransition("input requires an active listening turn")
        self._require_owner(device_id)
        if not text.strip():
            raise ValueError("input text cannot be empty")
        self._snapshot = replace(self._snapshot, phase=RuntimePhase.THINKING)
        return self._snapshot

    def mark_speaking(self, *, device_id: str) -> RuntimeSnapshot:
        if self._snapshot.phase is not RuntimePhase.THINKING:
            raise InvalidTransition("speech can begin only after thinking")
        self._require_owner(device_id)
        self._snapshot = replace(self._snapshot, phase=RuntimePhase.SPEAKING)
        return self._snapshot

    def mark_awaiting_approval(self, *, device_id: str) -> RuntimeSnapshot:
        if self._snapshot.phase not in {RuntimePhase.THINKING, RuntimePhase.SPEAKING}:
            raise InvalidTransition("approval can be requested only while thinking")
        self._require_owner(device_id)
        self._snapshot = replace(self._snapshot, phase=RuntimePhase.AWAITING_APPROVAL)
        return self._snapshot

    def mark_acting(self, *, device_id: str) -> RuntimeSnapshot:
        if self._snapshot.phase not in {
            RuntimePhase.THINKING,
            RuntimePhase.AWAITING_APPROVAL,
            RuntimePhase.SPEAKING,
        }:
            raise InvalidTransition("an action can begin only after thinking or approval")
        self._require_owner(device_id)
        self._snapshot = replace(self._snapshot, phase=RuntimePhase.ACTING)
        return self._snapshot

    def resume_thinking(self, *, device_id: str) -> RuntimeSnapshot:
        if self._snapshot.phase not in {
            RuntimePhase.ACTING,
            RuntimePhase.AWAITING_APPROVAL,
        }:
            raise InvalidTransition("tool results can resume only an action or approval turn")
        self._require_owner(device_id)
        self._snapshot = replace(self._snapshot, phase=RuntimePhase.THINKING)
        return self._snapshot

    def interrupt(self, *, device_id: str) -> RuntimeSnapshot:
        if self._snapshot.phase is RuntimePhase.IDLE:
            raise InvalidTransition("there is no active turn to interrupt")
        self._require_owner(device_id)
        self._snapshot = replace(
            self._snapshot,
            phase=RuntimePhase.LISTENING,
            cancellation_generation=self._snapshot.cancellation_generation + 1,
        )
        return self._snapshot

    def transfer_device(self, *, from_device_id: str, to_device_id: str) -> RuntimeSnapshot:
        if self._snapshot.phase is RuntimePhase.IDLE:
            raise InvalidTransition("there is no active turn to transfer")
        self._require_owner(from_device_id)
        if not to_device_id:
            raise ValueError("to_device_id cannot be empty")
        self._snapshot = replace(self._snapshot, active_device_id=to_device_id)
        return self._snapshot

    def set_mode(self, mode: ListeningMode) -> RuntimeSnapshot:
        self._snapshot = replace(self._snapshot, mode=mode)
        return self._snapshot

    def complete_turn(self) -> RuntimeSnapshot:
        if self._snapshot.phase is RuntimePhase.IDLE:
            raise InvalidTransition("there is no active turn to complete")
        self._snapshot = RuntimeSnapshot(
            mode=self._snapshot.mode,
            cancellation_generation=self._snapshot.cancellation_generation,
        )
        return self._snapshot

    def _require_owner(self, device_id: str) -> None:
        if self._snapshot.active_device_id != device_id:
            raise DeviceOwnershipError(
                f"active turn belongs to {self._snapshot.active_device_id!r}, not {device_id!r}"
            )
