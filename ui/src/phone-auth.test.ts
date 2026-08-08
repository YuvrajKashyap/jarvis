import { describe, expect, it } from "vitest";

import { parsePairingFragment } from "./phone-auth";

describe("phone pairing links", () => {
  it("keeps the one-use secret in the URL fragment and validates its shape", () => {
    const payload = btoa(
      JSON.stringify({
        pairingId: "019fd977-1d96-7892-950c-6afbb71f7cf0",
        secret: "a-secret-with-at-least-thirty-two-characters",
      }),
    )
      .replaceAll("+", "-")
      .replaceAll("/", "_")
      .replaceAll("=", "");

    expect(parsePairingFragment(`#pair=${payload}`)).toEqual({
      pairingId: "019fd977-1d96-7892-950c-6afbb71f7cf0",
      secret: "a-secret-with-at-least-thirty-two-characters",
    });
    expect(parsePairingFragment("#pair=not-valid-base64")).toBeNull();
    expect(parsePairingFragment("")).toBeNull();
  });
});
