import type { ReadinessSnapshot } from "./generated";
import { ConversationOverlay, type OverlayView } from "./overlay";
import "./showcase.css";

export type ShowcaseScenario = "conversation" | "readiness" | "approval" | "phone";

const NOOP = () => undefined;

export function showcaseScenario(search: string): ShowcaseScenario {
  const candidate = new URLSearchParams(search).get("scenario");
  return candidate === "readiness" || candidate === "approval" || candidate === "phone"
    ? candidate
    : "conversation";
}

export function Showcase({ scenario }: { scenario: ShowcaseScenario }) {
  const phone = scenario === "phone";
  return (
    <main className={`showcase showcase--${scenario}`}>
      <div className="showcase__workspace" aria-hidden="true">
        <div className="showcase__rail">
          <span />
          <span />
          <span />
          <span />
        </div>
        <div className="showcase__document">
          <div className="showcase__chrome">
            <i />
            <i />
            <i />
          </div>
          <div className="showcase__lines">
            {DOCUMENT_LINES.map((line) => (
              <span key={line.id} style={{ width: line.width }} />
            ))}
          </div>
        </div>
      </div>
      <div className="showcase__surface">
        <ConversationOverlay
          surface={phone ? "phone" : "desktop"}
          view={scenarioView(scenario)}
          readiness={scenario === "readiness" ? READINESS : null}
          onApprove={NOOP}
          onReject={NOOP}
          onInterrupt={NOOP}
          onSubmit={NOOP}
          onActivate={NOOP}
          onMoveOverlay={NOOP}
          onResetOverlay={NOOP}
          onRetryConnection={NOOP}
        />
      </div>
      <p className="showcase__caption">
        {phone ? "PRIVATE IPHONE COMPANION" : "LOCAL WINDOWS OVERLAY"}
        <span>{scenarioCaption(scenario)}</span>
      </p>
    </main>
  );
}

function scenarioView(scenario: ShowcaseScenario): OverlayView {
  if (scenario === "readiness") {
    return view({
      state: "idle",
      detail: "Hardware qualification paused",
    });
  }
  if (scenario === "approval") {
    return view({
      state: "awaiting_approval",
      detail: "Exact action held by policy",
      transcript: [
        {
          id: "approval-user",
          speaker: "user",
          text: "Send the prepared project update to the recruiter.",
          isFinal: true,
        },
      ],
      approval: {
        id: "approval-showcase",
        capability: "messages.send",
        summary: "Send the prepared project update to recruiter@example.com",
        risk: "external_irreversible",
      },
    });
  }
  if (scenario === "phone") {
    return view({
      state: "idle",
      detail: "Connected privately through Tailscale",
      transcript: [
        {
          id: "phone-user",
          speaker: "user",
          text: "What did we decide about the model?",
          isFinal: true,
        },
        {
          id: "phone-assistant",
          speaker: "assistant",
          text: "The software foundation is ready to continue, but this laptop needs more memory before a capable local model can remain resident safely.",
          isFinal: true,
        },
      ],
    });
  }
  return view({
    state: "idle",
    detail: "Grounded in current screen context",
    transcript: [
      {
        id: "conversation-user",
        speaker: "user",
        text: "JARVIS, what am I looking at?",
        isFinal: true,
      },
      {
        id: "conversation-assistant",
        speaker: "assistant",
        text: "Your test runner is open with one failing policy test. The other 253 checks passed. I can inspect the failure without changing anything.",
        isFinal: true,
      },
    ],
  });
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

function scenarioCaption(scenario: ShowcaseScenario): string {
  const captions: Record<ShowcaseScenario, string> = {
    conversation: "SCREEN-AWARE CONVERSATION",
    readiness: "EVIDENCE-BASED READINESS",
    approval: "DETERMINISTIC APPROVAL GATE",
    phone: "SHARED CONVERSATION STATE",
  };
  return captions[scenario];
}

const READINESS: ReadinessSnapshot = {
  overall: "blocked",
  generated_at: "2026-08-12T06:00:00Z",
  checks: [
    {
      code: "model_quality",
      state: "unverified",
      summary: "No local model has passed the complete JARVIS quality suite.",
      detail: "The evaluation requires grounded reasoning, reliable tool use, and safe latency.",
    },
    {
      code: "resource_pressure",
      state: "blocked",
      summary: "The current 16 GB laptop cannot sustain the minimum capable model safely.",
      detail: "0.66 GiB remained after loading Qwen3.5 4B Q4; JARVIS unloaded it automatically.",
    },
    {
      code: "physical_acceptance",
      state: "unverified",
      summary: "Voice, acoustic, soak, and physical iPhone acceptance remain pending.",
      detail: "Those gates resume after a model qualifies on upgraded hardware.",
    },
  ],
};

const DOCUMENT_LINES = Array.from({ length: 12 }, (_, index) => ({
  id: `line-${index + 1}`,
  width: `${42 + ((index * 19) % 49)}%`,
}));
