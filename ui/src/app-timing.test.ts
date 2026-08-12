import { describe, expect, it, vi } from "vitest";

import {
  desktopIdleHideDelay,
  overlayIsRelocating,
  overlayMotionEnabled,
  phoneConnectionFailureMessage,
  setOverlayTransitState,
  shouldFocusDesktopOverlay,
  submitFromComposer,
} from "./app";
import { PhoneRequestError, UnpairedPhoneError } from "./phone-auth";

describe("desktop overlay visibility", () => {
  it("keeps a manually revealed idle overlay usable long enough to start pairing", () => {
    expect(desktopIdleHideDelay(null, false)).toBeGreaterThanOrEqual(20_000);
    expect(
      desktopIdleHideDelay("Memory pressure is preventing model load.", false),
    ).toBeGreaterThanOrEqual(30_000);
  });

  it("keeps a completed conversation open for a natural follow-up", () => {
    expect(desktopIdleHideDelay(null, true)).toBeGreaterThanOrEqual(120_000);
  });

  it("disables native glide motion when Windows requests reduced motion", () => {
    expect(overlayMotionEnabled(() => ({ matches: false }))).toBe(true);
    expect(overlayMotionEnabled(() => ({ matches: true }))).toBe(false);
  });

  it("exposes native travel as a temporary visual continuity state", () => {
    const root = { dataset: {} as DOMStringMap };

    setOverlayTransitState(root, "cross-monitor");
    expect(root.dataset.overlayTransit).toBe("cross-monitor");

    setOverlayTransitState(root, null);
    expect(root.dataset.overlayTransit).toBeUndefined();
  });

  it("does not let content fitting interrupt a native relocation", () => {
    const root = { dataset: {} as DOMStringMap };
    setOverlayTransitState(root, "local");
    expect(overlayIsRelocating(root)).toBe(true);
    setOverlayTransitState(root, null);
    expect(overlayIsRelocating(root)).toBe(false);
  });

  it("keeps proactive movement in the background without stealing keyboard focus", () => {
    expect(shouldFocusDesktopOverlay("conversation")).toBe(true);
    expect(shouldFocusDesktopOverlay("proactive")).toBe(false);
    expect(shouldFocusDesktopOverlay(null)).toBe(false);
  });
});

describe("desktop composer", () => {
  it("activates a new turn before submitting from the idle state", () => {
    const client = {
      startTextTurn: vi.fn(),
      submitText: vi.fn(),
    };

    submitFromComposer(client, "idle", "Continue our conversation.");

    expect(client.startTextTurn).toHaveBeenCalledWith("Continue our conversation.");
    expect(client.submitText).not.toHaveBeenCalled();
  });
});

describe("phone connection guidance", () => {
  it("distinguishes an unpaired phone from an expired one-use QR code", () => {
    expect(phoneConnectionFailureMessage(new UnpairedPhoneError())).toMatch(/not paired/i);
    expect(phoneConnectionFailureMessage(new PhoneRequestError(400))).toMatch(/expired|used/i);
  });
});
