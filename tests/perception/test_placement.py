from jarvis.perception.placement import (
    ContentAwarePlacement,
    DesktopLayout,
    MonitorLayout,
    OverlayGeometry,
    PlacementIntent,
    PlacementRequest,
    Point,
    Rect,
    choose_overlay_placement,
    placement_regions,
)


def _monitor(left: int, *, name: str) -> MonitorLayout:
    return MonitorLayout(
        name=name,
        bounds=Rect(left=left, top=0, width=1920, height=1080),
        work_area=Rect(left=left, top=0, width=1920, height=1032),
    )


def _request(intent: PlacementIntent) -> PlacementRequest:
    return PlacementRequest(
        intent=intent,
        overlay=OverlayGeometry(left=580, top=776, width=760, height=224, visible=True),
        pointer=Point(x=960, y=440),
        monitors=(_monitor(0, name="primary"), _monitor(1920, name="right")),
    )


def test_active_conversation_never_leaves_the_users_monitor() -> None:
    request = _request(PlacementIntent.CONVERSATION)
    layout = DesktopLayout(
        region_density={(0, anchor): 0.72 for anchor in range(8)}
        | {(1, anchor): 0.01 for anchor in range(8)},
        attention=Rect(left=720, top=220, width=480, height=440),
    )

    plan = choose_overlay_placement(request, layout)

    assert plan.disposition == "place"
    assert 0 <= plan.target.left < 1920
    assert plan.monitor_name == "primary"


def test_background_suggestion_spills_to_the_nearest_clear_monitor() -> None:
    request = _request(PlacementIntent.PROACTIVE)
    layout = DesktopLayout(
        region_density={(0, anchor): 0.78 for anchor in range(8)}
        | {(1, anchor): 0.04 for anchor in range(8)},
        attention=Rect(left=620, top=180, width=680, height=620),
    )

    plan = choose_overlay_placement(request, layout)

    assert plan.disposition == "place"
    assert plan.monitor_name == "right"
    assert plan.target.left >= 1920


def test_background_suggestion_defers_when_every_monitor_is_full() -> None:
    request = _request(PlacementIntent.PROACTIVE)
    layout = DesktopLayout(
        region_density={(monitor, anchor): 0.9 for monitor in range(2) for anchor in range(8)},
        attention=Rect(left=0, top=0, width=1920, height=1032),
    )

    plan = choose_overlay_placement(request, layout)

    assert plan.disposition == "defer"
    assert plan.monitor_name is None


def test_clear_current_position_wins_over_a_marginally_quieter_corner() -> None:
    request = _request(PlacementIntent.CONVERSATION)
    layout = DesktopLayout(
        region_density={(0, anchor): 0.1 for anchor in range(8)},
        current_density=0.12,
        attention=Rect(left=40, top=40, width=320, height=240),
    )

    plan = choose_overlay_placement(request, layout)

    assert plan.target.left == request.overlay.left
    assert plan.target.top == request.overlay.top
    assert plan.reason == "current_position_is_clear"


def test_attention_overlap_outweighs_a_low_visual_density_score() -> None:
    request = _request(PlacementIntent.CONVERSATION)
    layout = DesktopLayout(
        region_density={(0, anchor): (0.01 if anchor == 0 else 0.18) for anchor in range(8)},
        attention=Rect(left=0, top=0, width=900, height=500),
    )

    plan = choose_overlay_placement(request, layout)

    assert plan.anchor != "top_left"
    assert not plan.target.intersects(layout.attention)


def test_content_aware_service_uses_an_ephemeral_layout_probe() -> None:
    request = _request(PlacementIntent.CONVERSATION)

    class FakeProbe:
        def inspect(self, request: PlacementRequest) -> DesktopLayout:
            assert request.intent is PlacementIntent.CONVERSATION
            return DesktopLayout(
                region_density={
                    (monitor, anchor): 0.05 for monitor in range(2) for anchor in range(8)
                },
                attention=Rect(left=600, top=200, width=720, height=500),
            )

    plan = ContentAwarePlacement(FakeProbe()).plan(request)

    assert plan.disposition == "place"


def test_planner_can_fold_into_a_tall_surface_when_that_is_the_quiet_shape() -> None:
    request = _request(PlacementIntent.CONVERSATION)
    regions = placement_regions(request)
    tall_index = next(
        index for monitor, index, region in regions if monitor == 0 and region.height > region.width
    )
    layout = DesktopLayout(
        region_density={(monitor, index): 0.88 for monitor, index, _ in regions}
        | {(0, tall_index): 0.02},
        attention=Rect(left=760, top=200, width=400, height=400),
    )

    plan = choose_overlay_placement(request, layout)

    assert plan.target.height > plan.target.width
    assert plan.anchor is not None and plan.anchor.startswith("adaptive_")
