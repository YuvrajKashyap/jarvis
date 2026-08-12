from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from jarvis.platform.acceptance import LocalAcceptanceEvidence


def packaged_environment(root: Path, *, port: int, token: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "JARVIS_DATA_DIRECTORY": str(root / "data"),
            "JARVIS_MEMORY_DIRECTORY": str(root / "memory"),
            "JARVIS_FILE_ROOTS": json.dumps([root.as_posix()]),
            "JARVIS_PORT": str(port),
            "JARVIS_MODEL_PREWARM_ENABLED": "false",
            "JARVIS_DESKTOP_SPEECH_ENABLED": "false",
            "JARVIS_PROACTIVITY_ENABLED": "false",
            "JARVIS_DESKTOP_SESSION_TOKEN": token,
        }
    )
    return environment


def run(executable: Path, *, port: int = 7343) -> dict[str, object]:
    target = executable.resolve(strict=True)
    if not target.is_file():
        raise ValueError("packaged core must be a file")
    token = secrets.token_urlsafe(32)
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with tempfile.TemporaryDirectory(prefix="jarvis-packaged-core-") as directory:
        root = Path(directory)
        process = subprocess.Popen(  # noqa: S603 - exact user-built executable
            [str(target)],
            env=packaged_environment(root, port=port, token=token),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
        )
        try:
            health = _wait_for_json(f"http://127.0.0.1:{port}/v1/health", timeout=45)
            diagnostics = _get_json(
                f"http://127.0.0.1:{port}/v1/diagnostics",
                token=token,
                timeout=20,
            )
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    raw_checks = diagnostics.get("checks")
    if not isinstance(raw_checks, list):
        raise RuntimeError("packaged diagnostics did not return a check list")
    checks = {
        str(check.get("code")): str(check.get("state"))
        for check in raw_checks
        if isinstance(check, dict)
    }
    result: dict[str, object] = {
        "health": health.get("status") == "ok",
        "protocol_version": health.get("protocol_version"),
        "authenticated_diagnostics": len(checks) >= 16,
        "speech_dependencies": checks.get("speech_dependencies") == "ready",
        "model_not_loaded": checks.get("model_residency") == "degraded",
    }
    if not all(value is True for key, value in result.items() if key != "protocol_version"):
        raise RuntimeError(f"packaged core acceptance failed: {result}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test the frozen JARVIS core")
    parser.add_argument("executable", type=Path)
    arguments = parser.parse_args()
    result = run(arguments.executable)
    LocalAcceptanceEvidence(_data_directory() / "acceptance").record_pass("packaged-core")
    print(json.dumps(result, sort_keys=True))


def _wait_for_json(url: str, *, timeout: float) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return _get_json(url, timeout=2)
        except (OSError, urllib.error.URLError, ValueError):
            time.sleep(0.25)
    raise RuntimeError("packaged core did not become healthy")


def _get_json(url: str, *, token: str | None = None, timeout: float) -> dict[str, object]:
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("packaged acceptance permits only HTTP loopback requests")
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    request = urllib.request.Request(url, headers=headers)  # noqa: S310
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        payload = json.loads(response.read(1_000_000))
    if not isinstance(payload, dict):
        raise ValueError("packaged core returned a non-object response")
    return payload


def _data_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
    return base / "JARVIS"


if __name__ == "__main__":
    main()
