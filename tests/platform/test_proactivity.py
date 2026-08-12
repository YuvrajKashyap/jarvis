from datetime import UTC, datetime, timedelta
from pathlib import Path

from jarvis.agency.proactivity import ProactivePriority, SuggestionReceipt, TopicPreference
from jarvis.perception.context import ActiveWindowSnapshot, SystemHealthSnapshot
from jarvis.platform.proactivity import WindowsProactiveProbe
from jarvis.platform.proactivity_sqlite import SQLiteProactivityLedger
from jarvis.platform.sqlite import SQLiteStore
from jarvis.runtime.resources import ResourceSnapshot

NOW = datetime(2026, 8, 10, 20, 0, tzinfo=UTC)


class FakePerception:
    def __init__(
        self,
        *,
        process_name: str = "Code.exe",
        title: str = "JARVIS - Visual Studio Code",
        memory_percent: float = 42,
        available_memory_bytes: int = 8 * 1024**3,
    ) -> None:
        self.process_name = process_name
        self.title = title
        self.memory_percent = memory_percent
        self.available_memory_bytes = available_memory_bytes

    def active_window(self) -> ActiveWindowSnapshot:
        return ActiveWindowSnapshot(
            title=self.title,
            process_id=42,
            process_name=self.process_name,
            executable_path=None,
            captured_at=NOW,
        )

    def system_health(self) -> SystemHealthSnapshot:
        return SystemHealthSnapshot(
            cpu_percent=18,
            memory_percent=self.memory_percent,
            available_memory_bytes=self.available_memory_bytes,
            captured_at=NOW,
        )


def probe(downloads: Path, perception: FakePerception | None = None) -> WindowsProactiveProbe:
    return WindowsProactiveProbe(
        perception=perception or FakePerception(),
        downloads_directory=downloads,
        focus_threshold=timedelta(minutes=70),
    )


class HotResources:
    def snapshot(self) -> ResourceSnapshot:
        return ResourceSnapshot(
            available_memory_bytes=4 * 1024**3,
            committed_memory_percent=70,
            gpu_temperature_c=86,
            gpu_memory_used_bytes=3 * 1024**3,
        )


def test_download_probe_baselines_existing_files_and_suggests_only_after_a_new_file_is_stable(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "already-there.pdf"
    existing.write_bytes(b"old")
    observation = probe(tmp_path)

    assert observation.scan(NOW) == ()
    downloaded = tmp_path / "research.pdf"
    downloaded.write_bytes(b"complete")
    assert observation.scan(NOW + timedelta(minutes=1)) == ()

    suggestions = observation.scan(NOW + timedelta(minutes=2))

    assert len(suggestions) == 1
    assert suggestions[0].topic == "downloads"
    assert suggestions[0].title == "Research PDF is ready"
    assert suggestions[0].suggested_prompt == "Summarize research.pdf and suggest where to file it."


def test_sustained_focus_session_offers_a_checkpoint_without_inspecting_screen_content(
    tmp_path: Path,
) -> None:
    observation = probe(tmp_path)

    assert observation.scan(NOW) == ()
    suggestions = observation.scan(NOW + timedelta(minutes=71))

    assert len(suggestions) == 1
    assert suggestions[0].topic == "focus"
    assert suggestions[0].message.startswith("You've been focused in Visual Studio Code")
    assert "screen" not in suggestions[0].reason.lower()


def test_memory_pressure_proposes_diagnostics_but_never_process_termination(tmp_path: Path) -> None:
    perception = FakePerception(memory_percent=93, available_memory_bytes=1024**3)
    observation = probe(tmp_path, perception)

    suggestions = observation.scan(NOW)

    assert len(suggestions) == 1
    assert suggestions[0].topic == "system_health"
    assert suggestions[0].priority is ProactivePriority.NORMAL
    assert suggestions[0].proposed_action is None
    assert "close" not in suggestions[0].suggested_prompt.lower()


def test_high_gpu_temperature_proposes_cooling_diagnostics_without_killing_work(
    tmp_path: Path,
) -> None:
    observation = WindowsProactiveProbe(
        perception=FakePerception(),
        resources=HotResources(),
        downloads_directory=tmp_path,
    )

    suggestions = observation.scan(NOW)

    assert len(suggestions) == 1
    assert suggestions[0].topic == "system_health.temperature"
    assert suggestions[0].priority is ProactivePriority.IMPORTANT
    assert suggestions[0].proposed_action is None


def test_proactivity_cooldowns_survive_a_runtime_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "jarvis.db"
    first_store = SQLiteStore(database_path)
    first_store.initialize()
    first = SQLiteProactivityLedger(first_store)
    first.record(
        SuggestionReceipt(
            fingerprint="downloads:private-hash",
            suggested_at=NOW,
        )
    )
    first_store.close()

    restarted_store = SQLiteStore(database_path)
    restarted_store.initialize()
    restarted = SQLiteProactivityLedger(restarted_store)

    assert restarted.recent(NOW - timedelta(hours=1)) == (
        SuggestionReceipt(
            fingerprint="downloads:private-hash",
            suggested_at=NOW,
        ),
    )


def test_proactivity_topic_preferences_survive_a_runtime_restart(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "jarvis.db")
    store.initialize()
    ledger = SQLiteProactivityLedger(store)
    preference = TopicPreference(
        topic="downloads",
        muted=True,
        snoozed_until=NOW + timedelta(hours=2),
        affinity=-1,
    )

    ledger.set_preference(preference)
    store.close()
    restarted_store = SQLiteStore(tmp_path / "jarvis.db")
    restarted_store.initialize()

    assert SQLiteProactivityLedger(restarted_store).preference("downloads") == preference
