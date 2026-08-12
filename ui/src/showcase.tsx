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
      {phone ? <PhoneBackdrop /> : <DesktopBackdrop scenario={scenario} />}
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

function DesktopBackdrop({ scenario }: { scenario: Exclude<ShowcaseScenario, "phone"> }) {
  if (scenario === "approval") return <MailDraft />;

  return (
    <section className="workbench" aria-hidden="true">
      <header className="workbench__titlebar">
        <span>JARVIS</span>
        <span>evaluate_models.py</span>
        <span className="window-controls">_ &nbsp; □ &nbsp; ×</span>
      </header>
      <aside className="workbench__activity">
        <span className="activity-mark activity-mark--active" />
        <span className="activity-mark" />
        <span className="activity-mark" />
        <span className="activity-mark" />
      </aside>
      <aside className="workbench__files">
        <strong>Explorer</strong>
        <p>JARVIS</p>
        <ul>
          <li>⌄ evaluations</li>
          <li className="file-indent">jarvis-v1.json</li>
          <li>⌄ scripts</li>
          <li className="file-indent file-active">evaluate_models.py</li>
          <li>⌄ src / jarvis</li>
          <li className="file-indent">bootstrap.py</li>
        </ul>
      </aside>
      <div className="workbench__editor">
        <div className="editor-tabs">
          <span>evaluate_models.py</span>
          <span>model_evaluation.py</span>
        </div>
        <pre>
          <code>
            <span className="code-line">
              <b>18</b>
              <i>candidate</i> = <em>"qwen3.5:9b-q4"</em>
            </span>
            <span className="code-line">
              <b>19</b>
              <i>suite</i> = EvaluationSuite.load(<em>"jarvis-v1"</em>)
            </span>
            <span className="code-line">
              <b>20</b>
            </span>
            <span className="code-line">
              <b>21</b>
              <i>result</i> = <u>await</u> evaluator.run(
            </span>
            <span className="code-line">
              <b>22</b> candidate=candidate,
            </span>
            <span className="code-line">
              <b>23</b> cases=suite.cases,
            </span>
            <span className="code-line">
              <b>24</b> enforce_policy=<u>True</u>,
            </span>
            <span className="code-line">
              <b>25</b> unload_on_pressure=<u>True</u>,
            </span>
            <span className="code-line">
              <b>26</b>)
            </span>
          </code>
        </pre>
        <div className={`terminal terminal--${scenario}`}>
          <div className="terminal__tabs">
            <span>TERMINAL</span>
            <span>OUTPUT</span>
            <span>PROBLEMS</span>
          </div>
          {scenario === "proactivity" ? (
            <pre>
              <span className="terminal-command">PS C:\dev\jarvis&gt; pnpm verify</span>
              <span>308 passed · 63 passed · 20 passed</span>
              <span className="terminal-failure">FAILED permission_replay_is_denied</span>
              <span>Expected: denied &nbsp; Received: approval_required</span>
              <span className="terminal-muted">main passed 27 minutes ago</span>
            </pre>
          ) : (
            <pre>
              <span className="terminal-command">PS C:\dev\jarvis&gt; pnpm model:evaluate</span>
              <span>candidate &nbsp; qwen3.5:9b-q4</span>
              <span>progress &nbsp;&nbsp; 14 / 32 cases</span>
              <span>remaining &nbsp; 18 minutes</span>
              <span className="terminal-muted">resource governor armed · unload after run</span>
            </pre>
          )}
        </div>
      </div>
      <footer className="workbench__status">
        <span>main*</span>
        <span>Python 3.11.9</span>
        <span>UTF-8</span>
      </footer>
    </section>
  );
}

function MailDraft() {
  return (
    <section className="mail" aria-hidden="true">
      <header className="mail__titlebar">
        <span>Mail</span>
        <div className="mail__search">
          <span>Search mail</span>
        </div>
        <span className="window-controls">_ &nbsp; □ &nbsp; ×</span>
      </header>
      <aside className="mail__folders">
        <button type="button">New message</button>
        <strong>Favorites</strong>
        <span>
          Inbox <b>4</b>
        </span>
        <span>
          Drafts <b>1</b>
        </span>
        <span>Sent</span>
        <span>Archive</span>
      </aside>
      <article className="mail__composer">
        <h1>Project update</h1>
        <dl>
          <div>
            <dt>To</dt>
            <dd>Maya Chen</dd>
          </div>
          <div>
            <dt>Subject</dt>
            <dd>JARVIS build update</dd>
          </div>
        </dl>
        <div className="mail__body">
          <p>Hi Maya,</p>
          <p>
            I finished the latest JARVIS build and documented the model and hardware findings. Here
            is the project link we discussed:
          </p>
          <p className="mail__link">github.com/YuvrajKashyap/jarvis</p>
          <p>Would love to hear what you think when you get a chance.</p>
          <p>
            Best,
            <br />
            Yuvraj
          </p>
        </div>
      </article>
    </section>
  );
}

function PhoneBackdrop() {
  return (
    <div className="phone-backdrop" aria-hidden="true">
      <div className="phone-status">
        <span>9:41</span>
        <span>● ●● ▰</span>
      </div>
      <div className="phone-home" />
    </div>
  );
}

function scenarioView(scenario: ShowcaseScenario): OverlayView {
  if (scenario === "proactivity") {
    return view({
      state: "idle",
      detail: "Watching JARVIS build",
      suggestion: {
        id: "build-regression",
        title: "Permission regression in the latest build",
        message:
          "One replay-protection test failed after the dependency update. The same test passed on main 27 minutes ago.",
        reason:
          "You asked me to watch JARVIS builds while you are coding. I have not changed any files.",
        suggestedPrompt: "Show me the permission regression and the smallest safe fix.",
        priority: "important",
      },
    });
  }
  if (scenario === "approval") {
    return view({
      state: "awaiting_approval",
      detail: "Draft ready",
      transcript: [
        {
          id: "approval-user",
          speaker: "user",
          text: "Send Maya the final project update and ask for feedback.",
          isFinal: true,
        },
        {
          id: "approval-assistant",
          speaker: "assistant",
          text: "The draft is ready. I have not sent it.",
          isFinal: true,
        },
      ],
      approval: {
        id: "approval-showcase",
        capability: "messages.send",
        summary: "Send the prepared update to Maya Chen",
        risk: "external_irreversible",
      },
    });
  }
  if (scenario === "phone") {
    return view({
      state: "idle",
      detail: "Continued from desktop",
      transcript: [
        {
          id: "phone-user",
          speaker: "user",
          text: "Pick up where we left off on the hardware plan.",
          isFinal: true,
        },
        {
          id: "phone-assistant",
          speaker: "assistant",
          text: "Picked up from your desktop. We ruled out the 9B Q4 on this laptop. Next is comparing 24 GB and 32 GB VRAM hosts; I kept the benchmark notes and open questions with this conversation.",
          isFinal: true,
        },
      ],
    });
  }
  return view({
    state: "idle",
    detail: "Evaluation · 18m remaining",
    transcript: [
      {
        id: "conversation-user",
        speaker: "user",
        text: "Can I leave this running while I head to practice?",
        isFinal: true,
      },
      {
        id: "conversation-assistant",
        speaker: "assistant",
        text: "Yes. The evaluation is isolated and has about 18 minutes left. Tennis starts at 4:30, so leave by 4:05. I will keep the model unloaded afterward and let you know if a safety case fails.",
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
