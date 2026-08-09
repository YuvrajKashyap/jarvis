import pytest

from jarvis.runtime.awareness import parse_awareness_command
from jarvis.runtime.conversation import ListeningMode


@pytest.mark.parametrize(
    ("text", "mode"),
    [
        ("Go completely private.", ListeningMode.PRIVATE),
        ("Start meeting mode", ListeningMode.MEETING),
        ("Remember this lecture", ListeningMode.LECTURE),
        ("Keep track of this conversation", ListeningMode.AMBIENT),
        ("Stop meeting mode", ListeningMode.NORMAL),
        ("Return to normal mode", ListeningMode.NORMAL),
    ],
)
def test_explicit_awareness_commands_map_to_modes(text: str, mode: ListeningMode) -> None:
    command = parse_awareness_command(text)

    assert command is not None
    assert command.mode is mode
    assert command.acknowledgement


@pytest.mark.parametrize(
    "text",
    [
        "Tell me why private mode matters",
        "Summarize the meeting notes",
        "I have a lecture tomorrow",
        "What were we talking about?",
    ],
)
def test_awareness_parser_does_not_infer_mode_changes_from_discussion(text: str) -> None:
    assert parse_awareness_command(text) is None
