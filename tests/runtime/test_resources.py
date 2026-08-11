from jarvis.platform.models import LoadedModel, ModelHealth
from jarvis.runtime.resources import (
    ModelResidency,
    ResourceGovernor,
    ResourceLimits,
    ResourcePressure,
    ResourceSnapshot,
)


class FakeModels:
    def __init__(self, *, resident: tuple[str, ...] = ()) -> None:
        self.loaded: list[str] = []
        self.unloaded: list[str] = []
        self.resident = resident

    async def health(self) -> ModelHealth:
        return ModelHealth(
            available=True,
            version="test",
            loaded_models=tuple(
                LoadedModel(
                    name=name,
                    size_bytes=3_000_000_000,
                    vram_bytes=3_000_000_000,
                    context_length=4096,
                )
                for name in self.resident
            ),
        )

    async def load(self, model: str) -> None:
        self.loaded.append(model)

    async def unload(self, model: str) -> None:
        self.unloaded.append(model)


class SequenceProbe:
    def __init__(self, snapshots: list[ResourceSnapshot]) -> None:
        self._snapshots = iter(snapshots)

    def snapshot(self) -> ResourceSnapshot:
        return next(self._snapshots)


def resources(available_gib: float, *, gpu_c: int = 55) -> ResourceSnapshot:
    return ResourceSnapshot(
        available_memory_bytes=int(available_gib * 1024**3),
        committed_memory_percent=100 - available_gib,
        gpu_temperature_c=gpu_c,
        gpu_memory_used_bytes=2 * 1024**3,
    )


def test_default_headroom_matches_the_measured_local_q4_workload() -> None:
    assert ResourceLimits().minimum_available_memory_bytes == 1024**3


async def test_governor_keeps_only_one_heavy_model_resident() -> None:
    models = FakeModels()
    probe = SequenceProbe([resources(5), resources(4), resources(4), resources(3)])
    governor = ResourceGovernor(
        models=models,
        probe=probe,
        limits=ResourceLimits(minimum_available_memory_bytes=2 * 1024**3),
    )

    first = await governor.ensure_resident("qwen3.5:4b-q8_0")
    second = await governor.ensure_resident("ministral-3:8b-instruct-2512-q4_K_M")

    assert first is ModelResidency.LOADED
    assert second is ModelResidency.LOADED
    assert models.loaded == ["qwen3.5:4b-q8_0", "ministral-3:8b-instruct-2512-q4_K_M"]
    assert models.unloaded == ["qwen3.5:4b-q8_0"]
    assert governor.resident_model == "ministral-3:8b-instruct-2512-q4_K_M"


async def test_governor_unloads_model_that_creates_unsafe_memory_pressure() -> None:
    models = FakeModels()
    governor = ResourceGovernor(
        models=models,
        probe=SequenceProbe([resources(3), resources(0.2)]),
        limits=ResourceLimits(minimum_available_memory_bytes=2 * 1024**3),
    )

    try:
        await governor.ensure_resident("qwen3.5:9b-q4_K_M")
    except ResourcePressure as error:
        assert error.reason == "available_memory"
    else:
        raise AssertionError("unsafe model load must be rejected")

    assert models.unloaded == ["qwen3.5:9b-q4_K_M"]
    assert governor.resident_model is None


async def test_governor_refuses_to_load_during_thermal_pressure() -> None:
    models = FakeModels()
    governor = ResourceGovernor(
        models=models,
        probe=SequenceProbe([resources(5, gpu_c=90)]),
        limits=ResourceLimits(maximum_gpu_temperature_c=85),
    )

    try:
        await governor.ensure_resident("qwen3.5:4b-q8_0")
    except ResourcePressure as error:
        assert error.reason == "gpu_temperature"
    else:
        raise AssertionError("thermal pressure must block model loading")

    assert models.loaded == []


async def test_resident_model_can_keep_serving_after_available_memory_dips() -> None:
    models = FakeModels()
    governor = ResourceGovernor(
        models=models,
        probe=SequenceProbe([resources(3), resources(2), resources(0.8)]),
        limits=ResourceLimits(minimum_available_memory_bytes=1024**3),
    )

    await governor.ensure_resident("qwen3.5:4b-q4_K_M")
    result = await governor.ensure_resident("qwen3.5:4b-q4_K_M")

    assert result is ModelResidency.ALREADY_RESIDENT
    assert models.loaded == ["qwen3.5:4b-q4_K_M"]


async def test_governor_recovers_an_ollama_resident_model_after_jarvis_restarts() -> None:
    models = FakeModels(resident=("qwen3.5:4b-q4_K_M",))
    governor = ResourceGovernor(
        models=models,
        probe=SequenceProbe([resources(0.6)]),
        limits=ResourceLimits(minimum_available_memory_bytes=1024**3),
    )

    result = await governor.ensure_resident("qwen3.5:4b-q4_K_M")

    assert result is ModelResidency.ALREADY_RESIDENT
    assert governor.resident_model == "qwen3.5:4b-q4_K_M"
    assert models.loaded == []


async def test_resident_model_still_stops_for_thermal_pressure() -> None:
    models = FakeModels()
    governor = ResourceGovernor(
        models=models,
        probe=SequenceProbe([resources(3), resources(2), resources(0.8, gpu_c=90)]),
        limits=ResourceLimits(maximum_gpu_temperature_c=85),
    )

    await governor.ensure_resident("qwen3.5:4b-q4_K_M")

    try:
        await governor.ensure_resident("qwen3.5:4b-q4_K_M")
    except ResourcePressure as error:
        assert error.reason == "gpu_temperature"
    else:
        raise AssertionError("thermal pressure must stop resident model use")
