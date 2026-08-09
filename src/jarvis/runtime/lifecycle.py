import asyncio
from typing import Protocol


class ModelResidency(Protocol):
    async def ensure_resident(self, model: str) -> object: ...

    async def unload(self) -> None: ...


class ApplicationLifecycle(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class Closeable(Protocol):
    def close(self) -> None: ...


class RuntimeLifecycle:
    """Owns startup and shutdown of resources that must remain resident."""

    def __init__(
        self,
        *,
        models: ModelResidency | None,
        primary_model: str | None,
        closeables: tuple[Closeable, ...] = (),
    ) -> None:
        if (models is None) != (primary_model is None):
            raise ValueError("model residency and primary model must be configured together")
        self._models = models
        self._primary_model = primary_model
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
        try:
            if self._models is not None:
                await self._models.unload()
        finally:
            for closeable in self._closeables:
                await asyncio.to_thread(closeable.close)
