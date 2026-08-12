import type { ServerEvent } from "./generated";
import type { OverlayView } from "./overlay";

export type SessionView = OverlayView & {
  lastSequence: number;
  lastSessionId: string | null;
  detailIsPersistent: boolean;
};

export function initialView(): SessionView {
  return {
    connection: "connected",
    state: "idle",
    transcript: [],
    approval: null,
    suggestion: null,
    detail: null,
    lastSequence: -1,
    lastSessionId: null,
    detailIsPersistent: false,
  };
}

export function reduceServerEvent(view: SessionView, event: ServerEvent): SessionView {
  if (event.session_id === view.lastSessionId && event.sequence <= view.lastSequence) {
    return view;
  }

  const next = {
    ...view,
    connection: "connected" as const,
    lastSequence: event.sequence,
    lastSessionId: event.session_id,
  };

  switch (event.type) {
    case "state_changed": {
      const preserveDetail =
        event.payload.state === "idle" && event.payload.detail === null && next.detailIsPersistent;
      return {
        ...next,
        state: event.payload.state,
        detail: preserveDetail ? next.detail : (event.payload.detail ?? null),
        detailIsPersistent: preserveDetail,
      };
    }
    case "transcript":
      return {
        ...next,
        transcript: [
          ...next.transcript,
          {
            id: event.event_id,
            speaker: event.payload.speaker,
            text: event.payload.text,
            isFinal: event.payload.is_final,
          },
        ],
      };
    case "assistant_text":
      return {
        ...next,
        transcript: mergeAssistantText(next.transcript, event),
      };
    case "approval_required":
      return {
        ...next,
        state: "awaiting_approval",
        approval: {
          id: event.payload.approval_id,
          capability: event.payload.capability,
          summary: event.payload.summary,
          risk: event.payload.risk,
        },
      };
    case "capability_result":
      return {
        ...next,
        approval: null,
        detail: event.payload.message,
        detailIsPersistent: true,
      };
    case "proactive_suggestion":
      return {
        ...next,
        suggestion: {
          id: event.payload.suggestion_id,
          title: event.payload.title,
          message: event.payload.message,
          reason: event.payload.reason,
          suggestedPrompt: event.payload.suggested_prompt,
          priority: event.payload.priority,
        },
      };
    case "error":
      return {
        ...next,
        detail: event.payload.message,
        detailIsPersistent: true,
      };
  }
}

export function shouldRevealDesktopOverlay(event: ServerEvent): boolean {
  if (event.type === "state_changed") return event.payload.state !== "idle";
  return (
    event.type === "capability_result" ||
    event.type === "approval_required" ||
    event.type === "proactive_suggestion"
  );
}

export type DesktopOverlayMovementIntent = "conversation" | "proactive";

export function desktopOverlayMovementIntent(
  event: ServerEvent,
): DesktopOverlayMovementIntent | null {
  if (event.type === "state_changed") {
    return ["listening", "private", "meeting", "lecture", "ambient"].includes(event.payload.state)
      ? "conversation"
      : null;
  }
  if (event.type === "proactive_suggestion") return "proactive";
  return isReminderNotification(event) ? "proactive" : null;
}

export function isReminderNotification(
  event: ServerEvent,
): event is Extract<ServerEvent, { type: "capability_result" }> {
  return (
    event.type === "capability_result" &&
    event.payload.capability === "notifications.remind" &&
    event.payload.status === "succeeded"
  );
}

function mergeAssistantText(
  transcript: OverlayView["transcript"],
  event: Extract<ServerEvent, { type: "assistant_text" }>,
): OverlayView["transcript"] {
  const lineId = `assistant:${event.turn_id}`;
  const lineIndex = transcript.findIndex((line) => line.id === lineId);
  if (lineIndex === -1) {
    return [
      ...transcript,
      {
        id: lineId,
        speaker: "assistant",
        text: event.payload.text,
        isFinal: event.payload.is_final,
      },
    ];
  }

  return transcript.map((line, index) =>
    index === lineIndex
      ? {
          ...line,
          text: `${line.text}${event.payload.text}`,
          isFinal: event.payload.is_final,
        }
      : line,
  );
}
