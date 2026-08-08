import type { ServerEvent } from "./generated";
import type { OverlayView } from "./overlay";

export type SessionView = OverlayView & {
  lastSequence: number;
  lastSessionId: string | null;
};

export function initialView(): SessionView {
  return {
    connection: "connected",
    state: "idle",
    transcript: [],
    approval: null,
    detail: null,
    lastSequence: -1,
    lastSessionId: null,
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
    case "state_changed":
      return { ...next, state: event.payload.state, detail: event.payload.detail ?? null };
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
      };
    case "error":
      return {
        ...next,
        detail: event.payload.message,
      };
  }
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
