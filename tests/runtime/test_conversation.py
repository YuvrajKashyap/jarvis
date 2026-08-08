from uuid import UUID

import pytest

from jarvis.runtime.conversation import (
    DeviceOwnershipError,
    InvalidTransition,
    ListeningMode,
    RuntimeCoordinator,
    RuntimePhase,
)

SESSION_ID = UUID("019fd977-1d96-7892-950c-6afbb71f7cf0")
TURN_ID = UUID("019fd977-1d96-7892-950c-6afbb71f7cf1")


def test_activation_claims_one_foreground_turn_for_the_initiating_device() -> None:
    runtime = RuntimeCoordinator()

    snapshot = runtime.activate(
        session_id=SESSION_ID,
        turn_id=TURN_ID,
        device_id="desktop",
    )

    assert snapshot.phase is RuntimePhase.LISTENING
    assert snapshot.session_id == SESSION_ID
    assert snapshot.turn_id == TURN_ID
    assert snapshot.active_device_id == "desktop"
    assert snapshot.cancellation_generation == 0


def test_input_requires_an_active_listening_turn_owned_by_the_device() -> None:
    runtime = RuntimeCoordinator()

    with pytest.raises(InvalidTransition):
        runtime.submit_input(device_id="desktop", text="hello")

    runtime.activate(session_id=SESSION_ID, turn_id=TURN_ID, device_id="desktop")
    with pytest.raises(DeviceOwnershipError):
        runtime.submit_input(device_id="phone", text="hello")

    snapshot = runtime.submit_input(device_id="desktop", text="hello")
    assert snapshot.phase is RuntimePhase.THINKING


def test_interruption_returns_to_listening_and_invalidates_inflight_work() -> None:
    runtime = RuntimeCoordinator()
    runtime.activate(session_id=SESSION_ID, turn_id=TURN_ID, device_id="desktop")
    runtime.submit_input(device_id="desktop", text="explain this")
    speaking = runtime.mark_speaking(device_id="desktop")

    interrupted = runtime.interrupt(device_id="desktop")

    assert speaking.phase is RuntimePhase.SPEAKING
    assert interrupted.phase is RuntimePhase.LISTENING
    assert interrupted.cancellation_generation == speaking.cancellation_generation + 1


def test_device_transfer_moves_input_and_audio_ownership_atomically() -> None:
    runtime = RuntimeCoordinator()
    runtime.activate(session_id=SESSION_ID, turn_id=TURN_ID, device_id="desktop")

    transferred = runtime.transfer_device(from_device_id="desktop", to_device_id="phone")

    assert transferred.active_device_id == "phone"
    with pytest.raises(DeviceOwnershipError):
        runtime.interrupt(device_id="desktop")
    assert runtime.interrupt(device_id="phone").phase is RuntimePhase.LISTENING


def test_completing_a_turn_returns_to_idle_without_resetting_privacy_mode() -> None:
    runtime = RuntimeCoordinator()
    runtime.set_mode(ListeningMode.PRIVATE)
    runtime.activate(session_id=SESSION_ID, turn_id=TURN_ID, device_id="desktop")
    runtime.submit_input(device_id="desktop", text="do not remember this")

    completed = runtime.complete_turn()

    assert completed.phase is RuntimePhase.IDLE
    assert completed.mode is ListeningMode.PRIVATE
    assert completed.session_id is None
    assert completed.turn_id is None
    assert completed.active_device_id is None


def test_a_new_activation_supersedes_an_active_turn_and_cancels_it() -> None:
    runtime = RuntimeCoordinator()
    runtime.activate(session_id=SESSION_ID, turn_id=TURN_ID, device_id="desktop")
    runtime.submit_input(device_id="desktop", text="long answer")
    before = runtime.snapshot()
    next_turn = UUID("019fd977-1d96-7892-950c-6afbb71f7cf2")

    after = runtime.activate(session_id=SESSION_ID, turn_id=next_turn, device_id="desktop")

    assert after.turn_id == next_turn
    assert after.phase is RuntimePhase.LISTENING
    assert after.cancellation_generation == before.cancellation_generation + 1


def test_capability_work_has_explicit_approval_and_acting_phases() -> None:
    runtime = RuntimeCoordinator()
    runtime.activate(session_id=SESSION_ID, turn_id=TURN_ID, device_id="desktop")
    runtime.submit_input(device_id="desktop", text="send the prepared message")

    waiting = runtime.mark_awaiting_approval(device_id="desktop")
    acting = runtime.mark_acting(device_id="desktop")

    assert waiting.phase is RuntimePhase.AWAITING_APPROVAL
    assert acting.phase is RuntimePhase.ACTING

    resumed = runtime.resume_thinking(device_id="desktop")

    assert resumed.phase is RuntimePhase.THINKING
