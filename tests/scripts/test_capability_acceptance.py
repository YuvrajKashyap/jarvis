import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "jarvis_capability_acceptance",
    Path(__file__).resolve().parents[2] / "scripts" / "capability_acceptance.py",
)
assert _SPEC is not None and _SPEC.loader is not None
capability_acceptance = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(capability_acceptance)


@pytest.mark.asyncio
async def test_capability_acceptance_uses_real_local_seams(tmp_path: Path) -> None:
    result = await capability_acceptance.run_capability_acceptance(tmp_path)

    assert result == {
        "observe": True,
        "approval_rejection": True,
        "write": True,
        "undo": True,
        "terminal": True,
        "device_binding": True,
        "forged_approval": True,
        "approval_replay": True,
        "destructive_rejection": True,
        "cancellation": True,
        "audit": True,
    }
