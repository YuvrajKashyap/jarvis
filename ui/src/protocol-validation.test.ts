import { describe, expect, it, vi } from "vitest";

import { ProtocolError, parseServerEvent } from "./protocol-validation";

const validState = {
  version: 1,
  event_id: "019fd977-1d96-7892-950c-6afbb71f7cf0",
  session_id: "019fd977-1d96-7892-950c-6afbb71f7cf1",
  turn_id: "019fd977-1d96-7892-950c-6afbb71f7cf2",
  sequence: 0,
  timestamp: "2026-08-07T18:30:00Z",
  type: "state_changed",
  payload: { state: "listening", detail: null },
};

describe("parseServerEvent", () => {
  it("returns a generated typed event only after schema validation", () => {
    const parsed = parseServerEvent(JSON.stringify(validState));

    expect(parsed.type).toBe("state_changed");
    if (parsed.type === "state_changed") {
      expect(parsed.payload.state).toBe("listening");
    }
  });

  it("rejects unknown fields, unknown event types, and invalid JSON", () => {
    expect(() =>
      parseServerEvent(JSON.stringify({ ...validState, hidden_command: "run" })),
    ).toThrow(ProtocolError);
    expect(() => parseServerEvent(JSON.stringify({ ...validState, type: "anything" }))).toThrow(
      ProtocolError,
    );
    expect(() => parseServerEvent("not-json")).toThrow(ProtocolError);
  });

  it("validates under the desktop content security policy without runtime code generation", async () => {
    vi.resetModules();
    vi.stubGlobal("Function", () => {
      throw new EvalError("runtime code generation is blocked");
    });

    try {
      const module = await import("./protocol-validation");
      expect(module.parseServerEvent(JSON.stringify(validState)).type).toBe("state_changed");
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
