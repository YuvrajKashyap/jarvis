import asyncio
from typing import Protocol

from jarvis.runtime.resources import ResourcePressure


class ModelResidency(Protocol):
    async def ensure_resident(self, model: str) -> object: ...

    async def unload(self) -> None: ...


class ApplicationLifecycle(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class Closeable(Protocol):
    def close(self) -> None: ...


class AsyncCloseable(Protocol):
    async def close(self) -> None: ...


class RuntimeLifecycle:
    """Owns startup and shutdown of resources that must remain resident."""

    def __init__(
        self,
        *,
        models: ModelResidency | None,
        primary_model: str | None,
        components: tuple[ApplicationLifecycle, ...] = (),
        async_closeables: tuple[AsyncCloseable, ...] = (),
        closeables: tuple[Closeable, ...] = (),
    ) -> None:
        if (models is None) != (primary_model is None):
            raise ValueError("model residency and primary model must be configured together")
        self._models = models
        self._primary_model = primary_model
        self._components = components
        self._active_components: list[ApplicationLifecycle] = []
        self._async_closeables = async_closeables
        self._closeables = closeables
        self._lock = asyncio.Lock()
        self._started = False

    async def start(self) -> None:
        async with self._lock:
            if self._started:
                return
            if self._models is not None and self._primary_model is not None:
                try:
                    await self._models.ensure_resident(self._primary_model)
                except ResourcePressure:
                    # Keep memory, schedules, transport, and diagnostics available. The next
                    # foreground turn retries residency and reports current resource pressure.
                    pass
                except BaseException:
                    await self._release()
                    raise
            try:
                for component in self._components:
                    await component.start()
                    self._active_components.append(component)
            except BaseException:
                await self._release()
                raise
            self._started = True

    async def stop(self) -> None:
        async with self._lock:
            if not self._started:
                return
            self._started = False
            await self._release()

    async def _release(self) -> None:
        failure: BaseException | None = None
        for component in reversed(self._active_components):
            try:
                await component.stop()
            except BaseException as error:
                failure = failure or error
        self._active_components.clear()
        if self._models is not None:
            try:
                await self._models.unload()
            except BaseException as error:
                failure = failure or error
        for closeable in self._async_closeables:
            try:
                await closeable.close()
            except BaseException as error:
                failure = failure or error
        for closeable in self._closeables:
            try:
                await asyncio.to_thread(closeable.close)
            except BaseException as error:
                failure = failure or error
        if failure is not None:
            raise failure
