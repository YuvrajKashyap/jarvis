import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "jarvis_packaged_core_acceptance",
    Path(__file__).resolve().parents[2] / "scripts" / "packaged_core_acceptance.py",
)
assert _SPEC is not None and _SPEC.loader is not None
packaged_core_acceptance = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(packaged_core_acceptance)


def test_packaged_environment_disables_hardware_work_and_uses_isolated_paths(
    tmp_path: Path,
) -> None:
    environment = packaged_core_acceptance.packaged_environment(
        tmp_path,
        port=7343,
        token="packaged-smoke-token-0123456789abcdef",
    )

    assert environment["JARVIS_DATA_DIRECTORY"] == str(tmp_path / "data")
    assert environment["JARVIS_MODEL_PREWARM_ENABLED"] == "false"
    assert environment["JARVIS_DESKTOP_SPEECH_ENABLED"] == "false"
    assert environment["JARVIS_PROACTIVITY_ENABLED"] == "false"
    assert environment["JARVIS_FILE_ROOTS"] == f'["{str(tmp_path).replace(chr(92), "/")}"]'
