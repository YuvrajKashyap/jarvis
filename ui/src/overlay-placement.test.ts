import { describe, expect, it, vi } from "vitest";

import { type NativePlacementContext, requestOverlayPlacement } from "./overlay-placement";

const context = {
  overlay: { left: 580, top: 776, width: 760, height: 224, visible: true },
  pointer: { x: 960, y: 440 },
  monitors: [
    {
      name: "primary",
      bounds: { left: 0, top: 0, width: 1920, height: 1080 },
      work_area: { left: 0, top: 0, width: 1920, height: 1032 },
    },
  ],
  auto_position: true,
} satisfies NativePlacementContext;

describe("content-aware overlay placement", () => {
  it("asks the local desktop-only planner for a typed target", async () => {
    const fetcher = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            disposition: "place",
            target: { left: 24, top: 24, width: 760, height: 224 },
            monitor_name: "primary",
            anchor: "top_left",
            reason: "clear_region_on_attention_monitor",
            density: 0.08,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
    );

    const plan = await requestOverlayPlacement(
      "ephemeral-desktop-token",
      context,
      "conversation",
      fetcher,
    );

    expect(fetcher).toHaveBeenCalledWith("http://127.0.0.1:7331/v1/overlay/placement", {
      method: "POST",
      cache: "no-store",
      credentials: "omit",
      headers: {
        Authorization: "Bearer ephemeral-desktop-token",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        intent: "conversation",
        overlay: context.overlay,
        pointer: context.pointer,
        monitors: context.monitors,
      }),
    });
    expect(plan?.target.left).toBe(24);
  });

  it("does not capture the desktop after manual placement", async () => {
    const fetcher = vi.fn();

    const plan = await requestOverlayPlacement(
      "ephemeral-desktop-token",
      { ...context, auto_position: false },
      "conversation",
      fetcher,
    );

    expect(plan).toBeNull();
    expect(fetcher).not.toHaveBeenCalled();
  });
});
