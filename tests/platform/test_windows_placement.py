from PIL import Image, ImageDraw

from jarvis.perception.placement import (
    MonitorLayout,
    OverlayGeometry,
    PlacementIntent,
    PlacementRequest,
    Point,
    Rect,
)
from jarvis.platform.windows import WindowsDesktopLayoutProbe


def test_visual_layout_probe_scores_text_heavy_regions_as_more_occupied() -> None:
    image = Image.new("RGB", (1920, 1080), "#202020")
    drawing = ImageDraw.Draw(image)
    for y in range(40, 440, 20):
        drawing.text((30, y), "Task Manager CPU Memory Disk Network", fill="white")
    request = PlacementRequest(
        intent=PlacementIntent.CONVERSATION,
        overlay=OverlayGeometry(left=580, top=776, width=760, height=224, visible=False),
        pointer=Point(x=960, y=540),
        monitors=(
            MonitorLayout(
                name="main",
                bounds=Rect(left=0, top=0, width=1920, height=1080),
                work_area=Rect(left=0, top=0, width=1920, height=1032),
            ),
        ),
    )

    layout = WindowsDesktopLayoutProbe(capture=lambda: image).inspect(request)

    assert layout.region_density[(0, 0)] > layout.region_density[(0, 7)]
    assert layout.current_density is not None
