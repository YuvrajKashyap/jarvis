import { describe, expect, it, vi } from "vitest";

import { enablePhonePwaUpdates, type ServiceWorkerRegistrar } from "./pwa-updates";

describe("phone PWA updates", () => {
  it("checks immediately and activates a waiting phone shell without requiring a hidden prompt", () => {
    const activate = vi.fn().mockResolvedValue(undefined);
    let needRefresh: (() => void) | undefined;
    const register = vi.fn(((options) => {
      needRefresh = options.onNeedRefresh;
      return activate;
    }) satisfies ServiceWorkerRegistrar);

    enablePhonePwaUpdates(register);
    needRefresh?.();

    expect(register).toHaveBeenCalledWith(
      expect.objectContaining({ immediate: true, onNeedRefresh: expect.any(Function) }),
    );
    expect(activate).toHaveBeenCalledWith(true);
  });
});
