import json
from pathlib import Path

from jarvis.platform.acceptance import LocalAcceptanceEvidence


def test_acceptance_evidence_is_model_specific_and_schema_validated(tmp_path: Path) -> None:
    directory = tmp_path / "acceptance"
    directory.mkdir()
    (directory / "model-quality.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "passed": True,
                "subject": "qwen3.5:4b-q4_K_M",
                "completed_at": "2026-08-11T12:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    evidence = LocalAcceptanceEvidence(directory)

    assert evidence.passed("model-quality", subject="qwen3.5:4b-q4_K_M") is True
    assert evidence.passed("model-quality", subject="qwen3.5:9b-q4_K_M") is False


def test_acceptance_evidence_rejects_unknown_files_and_false_results(tmp_path: Path) -> None:
    directory = tmp_path / "acceptance"
    directory.mkdir()
    (directory / "speech-pipeline.json").write_text(
        '{"schema_version":1,"passed":false,"completed_at":"2026-08-11T12:00:00Z"}',
        encoding="utf-8",
    )
    evidence = LocalAcceptanceEvidence(directory)

    assert evidence.passed("speech-pipeline") is False
    assert evidence.passed("../config") is False


def test_acceptance_writer_is_atomic_validated_and_subject_specific(tmp_path: Path) -> None:
    directory = tmp_path / "acceptance"
    evidence = LocalAcceptanceEvidence(directory)

    evidence.record_pass("model-quality", subject="qwen3.5:4b-q8_0")

    assert evidence.passed("model-quality", subject="qwen3.5:4b-q8_0") is True
    assert evidence.passing_subject("model-quality") == "qwen3.5:4b-q8_0"
    assert evidence.passed("model-quality", subject="qwen3.5:4b-q4_K_M") is False
    assert list(directory.glob("*.tmp")) == []


def test_passing_subject_never_trusts_failed_or_malformed_evidence(tmp_path: Path) -> None:
    directory = tmp_path / "acceptance"
    directory.mkdir()
    (directory / "model-quality.json").write_text(
        '{"schema_version":1,"passed":false,"subject":"unsafe","completed_at":"2026-08-11T12:00:00Z"}',
        encoding="utf-8",
    )
    evidence = LocalAcceptanceEvidence(directory)

    assert evidence.passing_subject("model-quality") is None
