from jarvis.runtime.lifecycle import RuntimeLifecycle
from jarvis.runtime.resources import ResourcePressure


class FakeResidency:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def ensure_resident(self, model: str) -> None:
        self.events.append(f"load:{model}")

    async def unload(self) -> None:
        self.events.append("unload")


class FakeCloseable:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def close(self) -> None:
        self._events.append("close:sqlite")


class FakeAsyncCloseable:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def close(self) -> None:
        self._events.append("close:browser")


class FakeComponent:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def start(self) -> None:
        self._events.append("start:scheduler")

    async def stop(self) -> None:
        self._events.append("stop:scheduler")


class FailingResidency(FakeResidency):
    async def ensure_resident(self, model: str) -> None:
        self.events.append(f"load:{model}")
        raise RuntimeError("model prewarm failed")


class PressuredResidency(FakeResidency):
    async def ensure_resident(self, model: str) -> None:
        self.events.append(f"load:{model}")
        raise ResourcePressure("available_memory")


async def test_runtime_lifecycle_prewarms_primary_model_and_releases_it() -> None:
    residency = FakeResidency()
    lifecycle = RuntimeLifecycle(
        models=residency,
        primary_model="qwen3.5:4b-q4_K_M",
    )

    await lifecycle.start()
    await lifecycle.stop()

    assert residency.events == ["load:qwen3.5:4b-q4_K_M", "unload"]


async def test_runtime_lifecycle_start_and_stop_are_idempotent() -> None:
    residency = FakeResidency()
    lifecycle = RuntimeLifecycle(
        models=residency,
        primary_model="qwen3.5:4b-q4_K_M",
    )

    await lifecycle.start()
    await lifecycle.start()
    await lifecycle.stop()
    await lifecycle.stop()

    assert residency.events == ["load:qwen3.5:4b-q4_K_M", "unload"]


async def test_runtime_lifecycle_releases_local_stores_after_model() -> None:
    residency = FakeResidency()
    lifecycle = RuntimeLifecycle(
        models=residency,
        primary_model="qwen3.5:4b-q4_K_M",
        closeables=(FakeCloseable(residency.events),),
    )

    await lifecycle.start()
    await lifecycle.stop()

    assert residency.events == [
        "load:qwen3.5:4b-q4_K_M",
        "unload",
        "close:sqlite",
    ]


async def test_runtime_lifecycle_releases_async_resources_before_local_stores() -> None:
    residency = FakeResidency()
    lifecycle = RuntimeLifecycle(
        models=residency,
        primary_model="qwen3.5:4b-q4_K_M",
        async_closeables=(FakeAsyncCloseable(residency.events),),
        closeables=(FakeCloseable(residency.events),),
    )

    await lifecycle.start()
    await lifecycle.stop()

    assert residency.events == [
        "load:qwen3.5:4b-q4_K_M",
        "unload",
        "close:browser",
        "close:sqlite",
    ]


async def test_runtime_lifecycle_starts_and_stops_managed_components() -> None:
    residency = FakeResidency()
    lifecycle = RuntimeLifecycle(
        models=residency,
        primary_model="qwen3.5:4b-q4_K_M",
        components=(FakeComponent(residency.events),),
    )

    await lifecycle.start()
    await lifecycle.stop()

    assert residency.events == [
        "load:qwen3.5:4b-q4_K_M",
        "start:scheduler",
        "stop:scheduler",
        "unload",
    ]


async def test_runtime_lifecycle_releases_resources_when_prewarm_fails() -> None:
    residency = FailingResidency()
    lifecycle = RuntimeLifecycle(
        models=residency,
        primary_model="qwen3.5:4b-q4_K_M",
        closeables=(FakeCloseable(residency.events),),
    )

    try:
        await lifecycle.start()
    except RuntimeError as error:
        assert str(error) == "model prewarm failed"
    else:
        raise AssertionError("expected prewarm failure")

    assert residency.events == [
        "load:qwen3.5:4b-q4_K_M",
        "unload",
        "close:sqlite",
    ]


async def test_runtime_stays_available_when_only_model_prewarm_is_resource_blocked() -> None:
    residency = PressuredResidency()
    lifecycle = RuntimeLifecycle(
        models=residency,
        primary_model="qwen3.5:4b-q4_K_M",
        components=(FakeComponent(residency.events),),
    )

    await lifecycle.start()
    await lifecycle.stop()

    assert residency.events == [
        "load:qwen3.5:4b-q4_K_M",
        "start:scheduler",
        "stop:scheduler",
        "unload",
    ]
