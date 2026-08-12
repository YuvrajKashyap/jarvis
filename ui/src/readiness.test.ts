import { describe, expect, it, vi } from "vitest";

import { readReadiness } from "./readiness";

const SNAPSHOT = {
  overall: "unverified",
  generated_at: "2026-08-11T15:30:00Z",
  checks: [
    {
      code: "voice_configuration",
      state: "unverified",
      summary: "No private JARVIS voice is configured.",
      detail: null,
    },
  ],
};

describe("readReadiness", () => {
  it("authenticates and validates the readiness response", async () => {
    const request = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(SNAPSHOT), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(readReadiness("http://127.0.0.1:7331", "secret", request)).resolves.toEqual(
      SNAPSHOT,
    );
    expect(request).toHaveBeenCalledWith("http://127.0.0.1:7331/v1/diagnostics", {
      cache: "no-store",
      credentials: "omit",
      headers: { Authorization: "Bearer secret" },
      signal: expect.any(AbortSignal),
    });
  });

  it("rejects malformed or unsuccessful diagnostic responses", async () => {
    const malformed = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ overall: "ready", checks: "not-an-array" }), {
        status: 200,
      }),
    );
    const unavailable = vi.fn().mockResolvedValue(new Response(null, { status: 503 }));

    await expect(readReadiness("https://jarvis.example", "token", malformed)).rejects.toThrow(
      "invalid readiness response",
    );
    await expect(readReadiness("https://jarvis.example", "token", unavailable)).rejects.toThrow(
      "readiness request failed",
    );
  });
});
