from pathlib import Path


def test_acceptance_runs_independent_gates_before_reporting_remaining_work() -> None:
    script = Path("scripts/acceptance.ps1").read_text(encoding="utf-8")

    assert "scripts/capability_acceptance.py" in script
    assert "scripts/recovery_acceptance.py" in script
    assert "scripts/evaluate_models.py" in script
    assert "scripts/preflight.py" in script
    assert "Acceptance failures:" in script
    assert "$failures.Add" in script
