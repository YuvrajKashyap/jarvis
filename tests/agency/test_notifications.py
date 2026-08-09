from datetime import UTC, datetime
from uuid import UUID

import pytest

from jarvis.agency.capabilities import CapabilityContext
from jarvis.agency.notifications import ReminderCapability, ReminderInput


@pytest.mark.asyncio
async def test_reminder_returns_bounded_user_facing_notification() -> None:
    capability = ReminderCapability()

    result = await capability.execute(
        ReminderInput(title="Tennis", message="Leave for practice in fifteen minutes."),
        CapabilityContext(
            invocation_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf0"),
            device_id="scheduler",
            requested_at=datetime(2026, 8, 9, 20, 0, tzinfo=UTC),
        ),
    )

    assert result.title == "Tennis"
    assert result.message == "Leave for practice in fifteen minutes."
