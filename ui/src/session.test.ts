import { describe, expect, it } from "vitest";

import type { ServerEvent } from "./generated";
import {
  initialView,
  isReminderNotification,
  reduceServerEvent,
  shouldRepositionDesktopOverlay,
  shouldRevealDesktopOverlay,
} from "./session";

const envelope = {
  version: 1 as const,
  event_id: "019fd977-1d96-7892-950c-6afbb71f7cf0",
  session_id: "019fd977-1d96-7892-950c-6afbb71f7cf1",
  turn_id: "019fd977-1d96-7892-950c-6afbb71f7cf2",
  sequence: 1,
  timestamp: "2026-08-07T18:30:00Z",
};

describe("reduceServerEvent", () => {
  it("reveals the desktop overlay for proactive results and active conversation state", () => {
    const reminder: ServerEvent = {
      ...envelope,
      type: "capability_result",
      payload: {
        action_id: "019fd977-1d96-7892-950c-6afbb71f7cf6",
        capability: "notifications.remind",
        status: "succeeded",
        message: "Leave for practice now.",
        undo_available: false,
      },
    };
    const idle: ServerEvent = {
      ...envelope,
      type: "state_changed",
      payload: { state: "idle", detail: null },
    };

    expect(shouldRevealDesktopOverlay(reminder)).toBe(true);
    expect(isReminderNotification(reminder)).toBe(true);
    expect(shouldRevealDesktopOverlay(idle)).toBe(false);
    expect(isReminderNotification(idle)).toBe(false);
  });

  it("relocates only at the start of an invocation or for a proactive result", () => {
    const listening: ServerEvent = {
      ...envelope,
      type: "state_changed",
      payload: { state: "listening", detail: "desktop" },
    };
    const thinking: ServerEvent = {
      ...envelope,
      type: "state_changed",
      payload: { state: "thinking", detail: null },
    };
    const reminder: ServerEvent = {
      ...envelope,
      type: "capability_result",
      payload: {
        action_id: "019fd977-1d96-7892-950c-6afbb71f7cf6",
        capability: "notifications.remind",
        status: "succeeded",
        message: "Leave for practice now.",
        undo_available: false,
      },
    };

    expect(shouldRepositionDesktopOverlay(listening)).toBe(true);
    expect(shouldRepositionDesktopOverlay(thinking)).toBe(false);
    expect(shouldRepositionDesktopOverlay(reminder)).toBe(true);
  });
  it("projects authoritative state changes into the overlay", () => {
    const event: ServerEvent = {
      ...envelope,
      type: "state_changed",
      payload: { state: "thinking", detail: "reading active window" },
    };

    const view = reduceServerEvent(initialView(), event);

    expect(view.state).toBe("thinking");
    expect(view.detail).toBe("reading active window");
  });

  it("keeps a turn error visible when the runtime returns to idle", () => {
    const error: ServerEvent = {
      ...envelope,
      type: "error",
      payload: {
        code: "resource_pressure",
        message: "Current memory pressure is too high for a safe response.",
        recoverable: true,
      },
    };
    const idle: ServerEvent = {
      ...envelope,
      sequence: 2,
      type: "state_changed",
      payload: { state: "idle", detail: null },
    };

    const view = [error, idle].reduce(reduceServerEvent, initialView());

    expect(view.state).toBe("idle");
    expect(view.detail).toBe("Current memory pressure is too high for a safe response.");
  });

  it("clears the active device label when a successful turn returns to idle", () => {
    const listening: ServerEvent = {
      ...envelope,
      type: "state_changed",
      payload: { state: "listening", detail: "desktop" },
    };
    const idle: ServerEvent = {
      ...envelope,
      sequence: 2,
      type: "state_changed",
      payload: { state: "idle", detail: null },
    };

    const view = [listening, idle].reduce(reduceServerEvent, initialView());

    expect(view.detail).toBeNull();
  });

  it("coalesces streamed assistant text by turn without losing prior user text", () => {
    const user: ServerEvent = {
      ...envelope,
      type: "transcript",
      payload: {
        text: "What am I looking at?",
        speaker: "user",
        is_final: true,
        device_id: "desktop",
      },
    };
    const first: ServerEvent = {
      ...envelope,
      event_id: "019fd977-1d96-7892-950c-6afbb71f7cf3",
      sequence: 2,
      type: "assistant_text",
      payload: { text: "Your editor ", is_final: false },
    };
    const second: ServerEvent = {
      ...envelope,
      event_id: "019fd977-1d96-7892-950c-6afbb71f7cf4",
      sequence: 3,
      type: "assistant_text",
      payload: { text: "is showing the JARVIS protocol.", is_final: true },
    };

    const view = [user, first, second].reduce(reduceServerEvent, initialView());

    expect(view.transcript).toHaveLength(2);
    expect(view.transcript[0]?.text).toBe("What am I looking at?");
    expect(view.transcript[1]).toMatchObject({
      speaker: "assistant",
      text: "Your editor is showing the JARVIS protocol.",
      isFinal: true,
    });
  });

  it("keeps only the current approval and clears it after a result", () => {
    const required: ServerEvent = {
      ...envelope,
      type: "approval_required",
      payload: {
        approval_id: "019fd977-1d96-7892-950c-6afbb71f7cf5",
        capability: "messages.send",
        summary: "Send the prepared email",
        risk: "external_irreversible",
        expires_at: "2026-08-07T18:31:00Z",
      },
    };
    const result: ServerEvent = {
      ...envelope,
      sequence: 2,
      type: "capability_result",
      payload: {
        action_id: "019fd977-1d96-7892-950c-6afbb71f7cf6",
        capability: "messages.send",
        status: "succeeded",
        message: "Email sent",
        undo_available: false,
      },
    };

    const awaiting = reduceServerEvent(initialView(), required);
    const completed = reduceServerEvent(awaiting, result);

    expect(awaiting.approval?.summary).toBe("Send the prepared email");
    expect(completed.approval).toBeNull();
    expect(completed.detail).toBe("Email sent");
  });

  it("drops replayed or out-of-order server events", () => {
    const newer: ServerEvent = {
      ...envelope,
      sequence: 10,
      type: "state_changed",
      payload: { state: "speaking", detail: null },
    };
    const older: ServerEvent = {
      ...envelope,
      sequence: 9,
      type: "state_changed",
      payload: { state: "thinking", detail: null },
    };

    const afterNew = reduceServerEvent(initialView(), newer);
    const afterOld = reduceServerEvent(afterNew, older);

    expect(afterOld.state).toBe("speaking");
    expect(afterOld.lastSequence).toBe(10);
  });
});
