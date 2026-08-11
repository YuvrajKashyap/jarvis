import asyncio
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from jarvis.platform.models import ModelHealth


class ResourceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    available_memory_bytes: int = Field(ge=0)
    committed_memory_percent: float = Field(ge=0, le=100)
    gpu_temperature_c: int | None = Field(default=None, ge=0, le=150)
    gpu_memory_used_bytes: int | None = Field(default=None, ge=0)


class ResourceLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_available_memory_bytes: int = Field(default=1024**3, ge=512 * 1024**2)
    maximum_gpu_temperature_c: int = Field(default=85, ge=50, le=100)


class ResourceProbe(Protocol):
    def snapshot(self) -> ResourceSnapshot: ...


class ModelLoader(Protocol):
    async def health(self) -> ModelHealth: ...

    async def load(self, model: str) -> None: ...

    async def unload(self, model: str) -> None: ...


class ModelResidency(StrEnum):
    LOADED = "loaded"
    ALREADY_RESIDENT = "already_resident"


class ResourcePressure(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ResourceGovernor:
    def __init__(
        self,
        *,
        models: ModelLoader,
        probe: ResourceProbe,
        limits: ResourceLimits,
    ) -> None:
        self._models = models
        self._probe = probe
        self._limits = limits
        self._lock = asyncio.Lock()
        self.resident_model: str | None = None

    async def ensure_resident(self, model: str) -> ModelResidency:
        async with self._lock:
            if self.resident_model == model:
                self._assert_thermally_safe(self._probe.snapshot())
                return ModelResidency.ALREADY_RESIDENT
            health = await self._models.health()
            if any(loaded.name == model for loaded in health.loaded_models):
                self._assert_thermally_safe(self._probe.snapshot())
                self.resident_model = model
                return ModelResidency.ALREADY_RESIDENT
            self._assert_safe(self._probe.snapshot())
            if self.resident_model is not None:
                await self._models.unload(self.resident_model)
                self.resident_model = None
            await self._models.load(model)
            try:
                self._assert_safe(self._probe.snapshot())
            except ResourcePressure:
                await self._models.unload(model)
                raise
            self.resident_model = model
            return ModelResidency.LOADED

    async def unload(self) -> None:
        async with self._lock:
            if self.resident_model is None:
                return
            model = self.resident_model
            self.resident_model = None
            await self._models.unload(model)

    def _assert_safe(self, snapshot: ResourceSnapshot) -> None:
        if snapshot.available_memory_bytes < self._limits.minimum_available_memory_bytes:
            raise ResourcePressure("available_memory")
        self._assert_thermally_safe(snapshot)

    def _assert_thermally_safe(self, snapshot: ResourceSnapshot) -> None:
        if (
            snapshot.gpu_temperature_c is not None
            and snapshot.gpu_temperature_c > self._limits.maximum_gpu_temperature_c
        ):
            raise ResourcePressure("gpu_temperature")
