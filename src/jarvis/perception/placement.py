from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import hypot
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class PlacementValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlacementIntent(StrEnum):
    CONVERSATION = "conversation"
    PROACTIVE = "proactive"


class Point(PlacementValue):
    x: float = Field(ge=-131_072, le=131_072)
    y: float = Field(ge=-131_072, le=131_072)


class Rect(PlacementValue):
    left: int = Field(ge=-131_072, le=131_072)
    top: int = Field(ge=-131_072, le=131_072)
    width: int = Field(gt=0, le=32_768)
    height: int = Field(gt=0, le=32_768)

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def center(self) -> Point:
        return Point(x=self.left + self.width / 2, y=self.top + self.height / 2)

    def contains(self, point: Point) -> bool:
        return self.left <= point.x < self.right and self.top <= point.y < self.bottom

    def intersects(self, other: Rect) -> bool:
        return not (
            self.right <= other.left
            or other.right <= self.left
            or self.bottom <= other.top
            or other.bottom <= self.top
        )

    def intersection_ratio(self, other: Rect) -> float:
        overlap_width = max(0, min(self.right, other.right) - max(self.left, other.left))
        overlap_height = max(0, min(self.bottom, other.bottom) - max(self.top, other.top))
        return (overlap_width * overlap_height) / (self.width * self.height)


class MonitorLayout(PlacementValue):
    name: str = Field(min_length=1, max_length=256)
    bounds: Rect
    work_area: Rect


class OverlayGeometry(Rect):
    visible: bool


class PlacementRequest(PlacementValue):
    intent: PlacementIntent
    overlay: OverlayGeometry
    pointer: Point
    monitors: tuple[MonitorLayout, ...] = Field(min_length=1, max_length=8)


class PlacementPlan(PlacementValue):
    disposition: Literal["place", "defer"]
    target: Rect
    monitor_name: str | None
    anchor: str | None
    reason: str
    density: float = Field(ge=0, le=1)


ANCHORS = (
    "top_left",
    "top_center",
    "top_right",
    "middle_left",
    "middle_right",
    "bottom_left",
    "bottom_center",
    "bottom_right",
)

CLEAR_DENSITY = 0.32
STABILITY_ALLOWANCE = 0.06


@dataclass(frozen=True)
class DesktopLayout:
    region_density: dict[tuple[int, int], float]
    attention: Rect
    current_density: float | None = None


class DesktopLayoutProbe(Protocol):
    def inspect(self, request: PlacementRequest) -> DesktopLayout: ...


class ContentAwarePlacement:
    def __init__(self, probe: DesktopLayoutProbe) -> None:
        self._probe = probe

    def plan(self, request: PlacementRequest) -> PlacementPlan:
        return choose_overlay_placement(request, self._probe.inspect(request))


@dataclass(frozen=True)
class _Candidate:
    monitor_index: int
    anchor: str
    region_index: int
    target: Rect
    density: float
    attention_overlap: float
    travel: float

    @property
    def score(self) -> float:
        return self.density + self.attention_overlap * 3.0 + min(self.travel / 12_000, 0.12)


def choose_overlay_placement(request: PlacementRequest, layout: DesktopLayout) -> PlacementPlan:
    active_monitor = _monitor_at(request.pointer, request.monitors)
    monitor_order = _monitor_order(active_monitor, request.monitors)
    candidates = _candidates(request, layout)

    if request.intent is PlacementIntent.CONVERSATION:
        eligible = [
            candidate for candidate in candidates if candidate.monitor_index == active_monitor
        ]
    else:
        eligible = []
        for monitor_index in monitor_order:
            monitor_candidates = [
                candidate for candidate in candidates if candidate.monitor_index == monitor_index
            ]
            if any(candidate.density <= CLEAR_DENSITY for candidate in monitor_candidates):
                eligible = monitor_candidates
                break
        if not eligible:
            return PlacementPlan(
                disposition="defer",
                target=Rect(
                    left=request.overlay.left,
                    top=request.overlay.top,
                    width=request.overlay.width,
                    height=request.overlay.height,
                ),
                monitor_name=None,
                anchor=None,
                reason="no_clear_background_region",
                density=1,
            )

    best = min(eligible, key=lambda candidate: candidate.score)
    current_density = layout.current_density
    current = Rect(
        left=request.overlay.left,
        top=request.overlay.top,
        width=request.overlay.width,
        height=request.overlay.height,
    )
    if (
        request.overlay.visible
        and current_density is not None
        and current_density <= CLEAR_DENSITY
        and not current.intersects(layout.attention)
        and current_density <= best.density + STABILITY_ALLOWANCE
        and request.monitors[active_monitor].work_area.contains(current.center)
    ):
        return PlacementPlan(
            disposition="place",
            target=current,
            monitor_name=request.monitors[active_monitor].name,
            anchor="preserve",
            reason="current_position_is_clear",
            density=current_density,
        )

    monitor = request.monitors[best.monitor_index]
    return PlacementPlan(
        disposition="place",
        target=best.target,
        monitor_name=monitor.name,
        anchor=best.anchor,
        reason=(
            "clear_region_on_attention_monitor"
            if best.monitor_index == active_monitor
            else "nearest_monitor_with_clear_region"
        ),
        density=best.density,
    )


def placement_request_schema() -> dict[str, object]:
    return PlacementRequest.model_json_schema()


def placement_plan_schema() -> dict[str, object]:
    return PlacementPlan.model_json_schema()


def _candidates(request: PlacementRequest, layout: DesktopLayout) -> list[_Candidate]:
    current_center = request.overlay.center
    candidates: list[_Candidate] = []
    for monitor_index, region_index, anchor, target in _region_specs(request):
        center = target.center
        candidates.append(
            _Candidate(
                monitor_index=monitor_index,
                anchor=anchor,
                region_index=region_index,
                target=target,
                density=min(
                    max(layout.region_density.get((monitor_index, region_index), 1), 0),
                    1,
                ),
                attention_overlap=target.intersection_ratio(layout.attention),
                travel=hypot(center.x - current_center.x, center.y - current_center.y),
            )
        )
    return candidates


def placement_regions(request: PlacementRequest) -> tuple[tuple[int, int, Rect], ...]:
    return tuple(
        (monitor_index, region_index, target)
        for monitor_index, region_index, _anchor, target in _region_specs(request)
    )


def _region_specs(request: PlacementRequest) -> tuple[tuple[int, int, str, Rect], ...]:
    regions: list[tuple[int, int, str, Rect]] = []
    for monitor_index, monitor in enumerate(request.monitors):
        geometries = [(anchor, request.overlay) for anchor in ANCHORS]
        for shape_index, geometry in enumerate(_adaptive_geometries(monitor, request.overlay)):
            geometries.extend((f"adaptive_{shape_index}_{anchor}", geometry) for anchor in ANCHORS)
        for region_index, (name, geometry) in enumerate(geometries):
            anchor = name.rsplit("_", maxsplit=2)[-2:]
            target = _at_anchor(monitor.work_area, geometry, "_".join(anchor))
            regions.append((monitor_index, region_index, name, target))
    return tuple(regions)


def _adaptive_geometries(
    monitor: MonitorLayout, overlay: OverlayGeometry
) -> tuple[OverlayGeometry, ...]:
    """Offer a bounded continuum of shapes while preserving the surface's content area."""
    available_width = max(1, monitor.work_area.width - 48)
    available_height = max(1, monitor.work_area.height - 48)
    content_area = overlay.width * overlay.height
    shapes: list[OverlayGeometry] = []
    seen = {(overlay.width, overlay.height)}
    for width_scale in (0.55, 0.7, 0.85, 1.15):
        width = min(available_width, max(320, round(overlay.width * width_scale)))
        height = min(available_height, max(224, round(content_area * 1.12 / width)))
        dimensions = (width, height)
        if dimensions in seen:
            continue
        seen.add(dimensions)
        shapes.append(
            OverlayGeometry(
                left=overlay.left,
                top=overlay.top,
                width=width,
                height=height,
                visible=overlay.visible,
            )
        )
    return tuple(shapes)


def _at_anchor(work_area: Rect, overlay: OverlayGeometry, anchor: str) -> Rect:
    margin = 24
    width = min(overlay.width, max(1, work_area.width - margin * 2))
    height = min(overlay.height, max(1, work_area.height - margin * 2))
    left = {
        "left": work_area.left + margin,
        "center": work_area.left + (work_area.width - width) // 2,
        "right": work_area.right - width - margin,
    }
    top = {
        "top": work_area.top + margin,
        "middle": work_area.top + (work_area.height - height) // 2,
        "bottom": work_area.bottom - height - margin,
    }
    vertical, horizontal = anchor.split("_", maxsplit=1)
    return Rect(left=left[horizontal], top=top[vertical], width=width, height=height)


def _monitor_at(point: Point, monitors: tuple[MonitorLayout, ...]) -> int:
    return next(
        (index for index, monitor in enumerate(monitors) if monitor.bounds.contains(point)),
        0,
    )


def _monitor_order(active: int, monitors: tuple[MonitorLayout, ...]) -> list[int]:
    origin = monitors[active].bounds.center
    return sorted(
        range(len(monitors)),
        key=lambda index: hypot(
            monitors[index].bounds.center.x - origin.x,
            monitors[index].bounds.center.y - origin.y,
        ),
    )
