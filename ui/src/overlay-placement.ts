import type { OverlayPlacementPlan, OverlayPlacementRequest } from "./generated";

export type NativePlacementContext = Omit<OverlayPlacementRequest, "intent"> & {
  auto_position: boolean;
};

type Fetcher = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export async function requestOverlayPlacement(
  desktopToken: string,
  context: NativePlacementContext,
  intent: OverlayPlacementRequest["intent"],
  fetcher: Fetcher = fetch,
): Promise<OverlayPlacementPlan | null> {
  if (!context.auto_position) return null;
  const request: OverlayPlacementRequest = {
    intent,
    overlay: context.overlay,
    pointer: context.pointer,
    monitors: context.monitors,
  };
  const response = await fetcher("http://127.0.0.1:7331/v1/overlay/placement", {
    method: "POST",
    cache: "no-store",
    credentials: "omit",
    headers: {
      Authorization: `Bearer ${desktopToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  });
  if (!response.ok) throw new Error("JARVIS could not inspect a safe overlay position.");
  return parsePlacementPlan(await response.json());
}

function parsePlacementPlan(value: unknown): OverlayPlacementPlan {
  if (!isRecord(value) || (value.disposition !== "place" && value.disposition !== "defer")) {
    throw new Error("JARVIS returned an invalid overlay placement.");
  }
  if (!isRect(value.target)) throw new Error("JARVIS returned an invalid overlay target.");
  if (
    (value.monitor_name !== null && typeof value.monitor_name !== "string") ||
    (value.anchor !== null && typeof value.anchor !== "string") ||
    typeof value.reason !== "string" ||
    !isFiniteNumber(value.density) ||
    value.density < 0 ||
    value.density > 1
  ) {
    throw new Error("JARVIS returned invalid overlay placement metadata.");
  }
  return value as unknown as OverlayPlacementPlan;
}

function isRect(value: unknown): boolean {
  return (
    isRecord(value) &&
    isFiniteNumber(value.left) &&
    isFiniteNumber(value.top) &&
    isFiniteNumber(value.width) &&
    value.width > 0 &&
    isFiniteNumber(value.height) &&
    value.height > 0
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}
