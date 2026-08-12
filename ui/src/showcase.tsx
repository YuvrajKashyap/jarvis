import { ConversationOverlay, JarvisOrb, type OverlayView } from "./overlay";
import "./showcase.css";

export type ShowcaseScenario =
  | "conversation"
  | "proactivity"
  | "memory"
  | "privacy"
  | "approval"
  | "phone"
  | "orb";

const NOOP = () => undefined;

export function showcaseScenario(search: string): ShowcaseScenario {
  const candidate = new URLSearchParams(search).get("scenario");
  const scenarios: ShowcaseScenario[] = [
    "conversation",
    "proactivity",
    "memory",
    "privacy",
    "approval",
    "phone",
    "orb",
  ];
  return scenarios.includes(candidate as ShowcaseScenario)
    ? (candidate as ShowcaseScenario)
    : "conversation";
}

export function Showcase({ scenario }: { scenario: ShowcaseScenario }) {
  if (scenario === "orb") {
    return (
      <main className="showcase showcase--orb">
        <div className="showcase__surface showcase__surface--orb">
          <JarvisOrb mode="resting" onActivate={NOOP} />
        </div>
      </main>
    );
  }

  const phone = scenario === "phone";
  return (
    <main className={`showcase showcase--${scenario}`}>
      <div className="showcase__surface">
        <ConversationOverlay
          surface={phone ? "phone" : "desktop"}
          view={scenarioView(scenario)}
          onApprove={NOOP}
          onReject={NOOP}
          onInterrupt={NOOP}
          onSubmit={NOOP}
          onActivate={NOOP}
          onMoveOverlay={NOOP}
          onResetOverlay={NOOP}
          onRetryConnection={NOOP}
          onDismissSuggestion={NOOP}
          onSuggestionFeedback={NOOP}
        />
      </div>
    </main>
  );
}

function scenarioView(scenario: ShowcaseScenario): OverlayView {
  if (scenario === "proactivity") {
    return view({
      detail: "Example | resource guard",
      suggestion: {
        id: "resource-guard-example",
        title: "The local model crossed its memory limit",
        message:
          "Available system memory fell below the 1 GiB safety floor while Qwen3.5 4B Q4 was loaded. I unloaded the model before it could pressure the rest of your desktop. No applications were closed.",
        reason: "You allowed me to watch resource pressure and protect normal desktop headroom.",
        suggestedPrompt: "Show me the model-load measurements.",
        actionLabel: "Show measurements",
        priority: "important",
      },
    });
  }
  if (scenario === "memory") {
    return view({
      detail: "Example | sourced memory",
      transcript: [
        {
          id: "memory-user",
          speaker: "user",
          text: "What did we learn from the 9B model test?",
          isFinal: true,
        },
        {
          id: "memory-assistant",
          speaker: "assistant",
          text: "It took 87.8 seconds to answer and left about 219 MB of available system memory, so it failed the responsiveness and safety gates on this laptop. The decision was to pause daily use until there is hardware with enough headroom for a stronger resident model.",
          isFinal: true,
        },
      ],
    });
  }
  if (scenario === "privacy") {
    return view({
      state: "private",
      detail: "Rolling buffer off",
      transcript: [
        {
          id: "privacy-user",
          speaker: "user",
          text: "Go completely private while I take this call.",
          isFinal: true,
        },
        {
          id: "privacy-assistant",
          speaker: "assistant",
          text: "Private mode is on. Wake-word detection remains available, but the 120-second rolling audio buffer is disabled and nothing from the room will be stored.",
          isFinal: true,
        },
      ],
    });
  }
  if (scenario === "approval") {
    return view({
      state: "awaiting_approval",
      detail: "Interface preview",
      approval: {
        id: "approval-preview",
        capability: "messages.send",
        summary:
          "No message will be sent. This view shows the approval boundary for external actions.",
        risk: "external_irreversible",
      },
    });
  }
  if (scenario === "phone") {
    return view({ detail: "Private companion" });
  }
  return view({});
}

function view(overrides: Partial<OverlayView>): OverlayView {
  return {
    connection: "connected",
    state: "idle",
    transcript: [],
    approval: null,
    suggestion: null,
    detail: null,
    ...overrides,
  };
}
