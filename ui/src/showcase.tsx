import { ConversationOverlay, type OverlayView } from "./overlay";
import "./showcase.css";

export type ShowcaseScenario = "conversation" | "proactivity" | "approval" | "phone";

const NOOP = () => undefined;

export function showcaseScenario(search: string): ShowcaseScenario {
  const candidate = new URLSearchParams(search).get("scenario");
  return candidate === "proactivity" || candidate === "approval" || candidate === "phone"
    ? candidate
    : "conversation";
}

export function Showcase({ scenario }: { scenario: ShowcaseScenario }) {
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
      detail: "Suggestion preview",
      suggestion: {
        id: "suggestion-preview",
        title: "Proactive assistance",
        message:
          "Authorized event suggestions appear here. JARVIS can explain why it surfaced them before you respond.",
        reason: "This interface only appears for events you have allowed JARVIS to monitor.",
        suggestedPrompt: "Tell me more.",
        priority: "normal",
      },
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
