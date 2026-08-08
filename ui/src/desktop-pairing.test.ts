import { describe, expect, it, vi } from "vitest";

import { createDesktopPairingOffer } from "./desktop-pairing";

describe("desktop phone pairing", () => {
  it("uses the ephemeral desktop credential and accepts only an HTTPS fragment link", async () => {
    const fetcher = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            pairing_id: "019fd977-1d96-7892-950c-6afbb71f7cf0",
            expires_at: "2026-08-08T02:05:00Z",
            pairing_url: "https://yuvraj-omen.example.ts.net/#pair=one-use-secret",
          }),
          { status: 201, headers: { "Content-Type": "application/json" } },
        ),
    );

    const offer = await createDesktopPairingOffer("ephemeral-desktop-token", fetcher);

    expect(fetcher).toHaveBeenCalledWith("http://127.0.0.1:7331/v1/pairing/offers", {
      method: "POST",
      cache: "no-store",
      credentials: "omit",
      headers: { Authorization: "Bearer ephemeral-desktop-token" },
    });
    expect(offer.pairingUrl).toContain("/#pair=");
  });

  it("rejects a missing tailnet URL instead of exposing a partial secret", async () => {
    const fetcher = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            pairing_id: "019fd977-1d96-7892-950c-6afbb71f7cf0",
            expires_at: "2026-08-08T02:05:00Z",
            pairing_url: null,
          }),
          { status: 201, headers: { "Content-Type": "application/json" } },
        ),
    );

    await expect(createDesktopPairingOffer("ephemeral-desktop-token", fetcher)).rejects.toThrow(
      "Tailscale Serve is not configured",
    );
  });
});
