import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "jarvis_recovery_acceptance",
    Path(__file__).resolve().parents[2] / "scripts" / "recovery_acceptance.py",
)
assert _SPEC is not None and _SPEC.loader is not None
recovery_acceptance = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(recovery_acceptance)


def test_recovery_acceptance_proves_backup_restore_and_rollback(tmp_path: Path) -> None:
    result = recovery_acceptance.run_recovery_acceptance(tmp_path)

    assert result == {
        "migration": True,
        "backup": True,
        "restore": True,
        "rollback": True,
        "corruption_rejected": True,
    }
