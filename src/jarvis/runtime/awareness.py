import re
from dataclasses import dataclass

from jarvis.runtime.conversation import ListeningMode

_SPACE = re.compile(r"\s+")
_TRAILING_PUNCTUATION = re.compile(r"[.!?]+$")


@dataclass(frozen=True)
class AwarenessCommand:
    mode: ListeningMode
    acknowledgement: str


def parse_awareness_command(text: str) -> AwarenessCommand | None:
    normalized = _TRAILING_PUNCTUATION.sub("", _SPACE.sub(" ", text.strip().casefold()))
    if normalized in {
        "go private",
        "go completely private",
        "start private mode",
        "turn on private mode",
        "don't remember anything",
        "do not remember anything",
    }:
        return AwarenessCommand(
            mode=ListeningMode.PRIVATE,
            acknowledgement=(
                "Private mode is on. I'll keep only wake-word detection and won't retain this "
                "turn or ambient audio."
            ),
        )
    if normalized in {"start meeting mode", "begin meeting mode"}:
        return AwarenessCommand(
            mode=ListeningMode.MEETING,
            acknowledgement=(
                "Meeting mode is on. I'll transcribe ambient speech locally until you tell me "
                "to stop."
            ),
        )
    if normalized in {
        "start lecture mode",
        "begin lecture mode",
        "remember this lecture",
    }:
        return AwarenessCommand(
            mode=ListeningMode.LECTURE,
            acknowledgement=(
                "Lecture mode is on. I'll preserve the spoken material locally until you stop "
                "the mode."
            ),
        )
    if normalized in {
        "start ambient memory mode",
        "begin ambient memory mode",
        "keep track of this conversation",
    }:
        return AwarenessCommand(
            mode=ListeningMode.AMBIENT,
            acknowledgement=(
                "Ambient memory mode is on. I'll retain local transcripts until you tell me to "
                "stop."
            ),
        )
    if normalized in {
        "stop meeting mode",
        "stop lecture mode",
        "stop ambient memory mode",
        "stop private mode",
        "leave private mode",
        "return to normal mode",
        "go back to normal mode",
    }:
        return AwarenessCommand(
            mode=ListeningMode.NORMAL,
            acknowledgement="Normal mode is restored. I'm back to wake-word-only listening.",
        )
    return None
