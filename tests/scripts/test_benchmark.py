import importlib.util
from pathlib import Path

import httpx

_SPEC = importlib.util.spec_from_file_location(
    "jarvis_benchmark",
    Path(__file__).resolve().parents[2] / "scripts" / "benchmark.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_BENCHMARK = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BENCHMARK)
run_prompt = _BENCHMARK.run_prompt


def test_model_capability_rejection_is_recorded_without_aborting_benchmark() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "model does not support tools"})

    with httpx.Client(
        base_url="http://127.0.0.1:11434",
        transport=httpx.MockTransport(handler),
    ) as client:
        result = run_prompt(
            client,
            model="gemma3:4b-it-qat",
            name="tool_selection",
            messages=[{"role": "user", "content": "Inspect the active window."}],
            context_tokens=4_096,
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "get_active_window",
                        "description": "Read foreground-window metadata.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            passed=lambda _response, tool: tool == "get_active_window",
        )

    assert result.passed is False
    assert result.error == "model does not support tools"
    assert result.response == ""
    assert result.tool_call_name is None
