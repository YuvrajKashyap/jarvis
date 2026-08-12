import { useLayoutEffect, useRef, useState } from "react";

import type { ReadinessSnapshot } from "./generated";
import type { ProactivityFeedback } from "./proactivity-feedback";

export type OverlayView = {
  connection: "connected" | "reconnecting" | "unavailable";
  state:
    | "idle"
    | "listening"
    | "transcribing"
    | "thinking"
    | "acting"
    | "awaiting_approval"
    | "speaking"
    | "private"
    | "meeting"
    | "lecture"
    | "ambient"
    | "unavailable";
  transcript: Array<{
    id: string;
    speaker: "user" | "assistant" | "ambient";
    text: string;
    isFinal: boolean;
  }>;
  approval: {
    id: string;
    capability: string;
    summary: string;
    risk: "local_reversible" | "external_irreversible";
  } | null;
  suggestion: {
    id: string;
    title: string;
    message: string;
    reason: string;
    suggestedPrompt: string;
    priority: "quiet" | "normal" | "important";
  } | null;
  detail: string | null;
};

type ConversationOverlayProps = {
  surface: "desktop" | "phone";
  view: OverlayView;
  readiness?: ReadinessSnapshot | null;
  onApprove: (approvalId: string) => void;
  onReject: (approvalId: string) => void;
  onInterrupt: () => void;
  onSubmit: (text: string) => void;
  onActivate: () => void;
  onMoveOverlay?: () => void;
  onResetOverlay?: () => void;
  onRetryConnection?: () => void;
  onDismissSuggestion?: () => void;
  onSuggestionFeedback?: (feedback: ProactivityFeedback) => void;
  pairing?: {
    qrDataUrl: string | null;
    expiresAt: string | null;
    error: string | null;
  } | null;
  onPairPhone?: () => void;
  onClosePairing?: () => void;
};

export function ConversationOverlay(_props: ConversationOverlayProps) {
  const {
    surface,
    view,
    readiness,
    onApprove,
    onReject,
    onInterrupt,
    onSubmit,
    onActivate,
    onMoveOverlay,
    onResetOverlay,
    onRetryConnection,
    onDismissSuggestion,
    onSuggestionFeedback,
    pairing,
    onPairPhone,
    onClosePairing,
  } = _props;
  const [draft, setDraft] = useState("");
  const transcriptRef = useRef<HTMLOListElement>(null);

  useLayoutEffect(() => {
    if (view.transcript.length === 0) return;
    const transcript = transcriptRef.current;
    if (!transcript) return;
    transcript.scrollTop = transcript.scrollHeight;
  }, [view.transcript]);

  if (surface === "phone" && view.connection !== "connected") {
    const connecting = view.connection === "reconnecting";
    return (
      <main className="phone-shell">
        <section className="connection-card" aria-describedby="connection-detail">
          <span className="eyebrow">Private companion</span>
          <h1>{connecting ? "Connecting to JARVIS" : "JARVIS connection failed"}</h1>
          <p id="connection-detail">
            {connecting
              ? "Securing a private connection to your laptop."
              : (view.detail ?? "Your laptop host could not be reached.")}
          </p>
          <div
            className={`horizon ${connecting ? "horizon--connecting" : "horizon--quiet"}`}
            aria-hidden="true"
          >
            {connecting ? <span className="horizon__signal" /> : null}
          </div>
          {!connecting ? (
            <button className="button button--retry" type="button" onClick={onRetryConnection}>
              Try again
            </button>
          ) : null}
        </section>
      </main>
    );
  }

  const action = view.approval?.capability.split(".").at(-1) ?? "action";
  const stateLabel = STATE_LABELS[view.state];
  const readinessIssues = readiness?.checks.filter((check) => check.state !== "ready") ?? [];

  return (
    <section className={`overlay overlay--${surface}`} aria-label="JARVIS conversation">
      <div className="overlay__content">
        <header className="status-row">
          <div className="brand-cluster">
            {surface === "desktop" ? (
              <button
                className="drag-handle"
                type="button"
                aria-label="Move JARVIS"
                title="Drag to move · double-click to reset"
                onPointerDown={onMoveOverlay}
                onDoubleClick={onResetOverlay}
              >
                <span aria-hidden="true" />
              </button>
            ) : null}
            <span className="wordmark" aria-hidden="true">
              JARVIS
            </span>
          </div>
          <span className="state" role="status" aria-live="polite">
            {stateLabel}
            {view.detail ? <span className="state-detail"> | {view.detail}</span> : null}
          </span>
        </header>

        <div
          className={`horizon horizon--${view.state}`}
          role="img"
          aria-label={`JARVIS is ${view.state.replace("_", " ")}`}
        >
          <span className="horizon__signal" aria-hidden="true" />
        </div>

        {!pairing && view.state === "idle" && readiness && readiness.overall !== "ready" ? (
          <details className={`readiness readiness--${readiness.overall}`}>
            <summary>
              <span>
                {readiness.overall === "blocked" ? "Not daily-ready" : "Setup incomplete"}
              </span>
              <span>{readinessIssues.length} checks need attention</span>
            </summary>
            <ol>
              {readinessIssues.map((check) => (
                <li key={check.code}>
                  <span className={`readiness__state readiness__state--${check.state}`}>
                    {check.state}
                  </span>
                  <div>
                    <p>{check.summary}</p>
                    {check.detail ? <small>{check.detail}</small> : null}
                  </div>
                </li>
              ))}
            </ol>
          </details>
        ) : null}

        {surface === "desktop" &&
        view.state === "idle" &&
        view.transcript.length === 0 &&
        !view.suggestion &&
        !pairing ? (
          <button className="pair-phone" type="button" onClick={onPairPhone}>
            Pair iPhone
          </button>
        ) : null}

        {surface === "desktop" && pairing ? (
          <section
            className="pairing-card"
            role="dialog"
            aria-modal="false"
            aria-labelledby="pairing-title"
          >
            <div className="pairing-card__copy">
              <span className="eyebrow">Private phone companion</span>
              <h2 id="pairing-title">Pair iPhone</h2>
              {pairing.error ? (
                <p role="alert">{pairing.error}</p>
              ) : (
                <p>Scan this one-use code from your iPhone. It expires in five minutes.</p>
              )}
            </div>
            {pairing.qrDataUrl ? (
              <img
                className="pairing-card__qr"
                src={pairing.qrDataUrl}
                alt="One-use iPhone pairing code"
              />
            ) : null}
            <button
              className="button button--quiet"
              type="button"
              aria-label="Close pairing"
              onClick={onClosePairing}
            >
              Close
            </button>
          </section>
        ) : null}

        {surface === "phone" && view.state === "idle" ? (
          <button className="call-jarvis" type="button" onClick={onActivate}>
            Talk to JARVIS
          </button>
        ) : null}

        {surface === "phone" && view.state === "idle" && view.transcript.length === 0 ? (
          <details className="phone-install">
            <summary>Keep JARVIS one tap away</summary>
            <ol>
              <li>In Safari, tap Share, then Add to Home Screen.</li>
              <li>Create an “Open JARVIS” Shortcut that opens this private address.</li>
              <li>Assign that Shortcut to the Action Button, or ask Siri to open JARVIS.</li>
            </ol>
          </details>
        ) : null}

        {!pairing && view.suggestion && view.transcript.length === 0 ? (
          <section
            className={`suggestion suggestion--${view.suggestion.priority}`}
            aria-label="JARVIS suggestion"
          >
            <div className="suggestion__copy">
              <span className="suggestion__signal">Worth noting</span>
              <h2>{view.suggestion.title}</h2>
              <p>{view.suggestion.message}</p>
              <details className="suggestion__reason">
                <summary>Why I mentioned it</summary>
                <p>{view.suggestion.reason}</p>
              </details>
            </div>
            <div className="suggestion__actions">
              <button
                className="button button--quiet"
                type="button"
                onClick={() =>
                  onSuggestionFeedback ? onSuggestionFeedback("dismiss") : onDismissSuggestion?.()
                }
              >
                Not now
              </button>
              <button
                className="button button--quiet"
                type="button"
                onClick={() => onSuggestionFeedback?.("snooze")}
              >
                Snooze
              </button>
              <details className="suggestion__tuning">
                <summary>Tune</summary>
                <div>
                  <button type="button" onClick={() => onSuggestionFeedback?.("less")}>
                    Less like this
                  </button>
                  <button type="button" onClick={() => onSuggestionFeedback?.("more")}>
                    More like this
                  </button>
                  <button type="button" onClick={() => onSuggestionFeedback?.("mute_topic")}>
                    Mute topic
                  </button>
                </div>
              </details>
              <button
                className="button button--suggestion"
                type="button"
                onClick={() => {
                  onDismissSuggestion?.();
                  onSubmit(view.suggestion?.suggestedPrompt ?? "");
                }}
              >
                Talk it through
              </button>
            </div>
          </section>
        ) : null}

        {!pairing && view.transcript.length > 0 ? (
          <ol ref={transcriptRef} className="transcript" aria-label="Conversation transcript">
            {view.transcript.map((line) => (
              <li className={`utterance utterance--${line.speaker}`} key={line.id}>
                <span className="speaker">{line.speaker === "user" ? "You" : "JARVIS"}</span>
                <p>{line.text}</p>
              </li>
            ))}
          </ol>
        ) : null}

        {view.state === "listening" || (surface === "desktop" && view.state === "idle") ? (
          <form
            className="message-box"
            aria-label="Message JARVIS"
            onSubmit={(event) => {
              event.preventDefault();
              const text = draft.trim();
              if (!text) return;
              onSubmit(text);
              setDraft("");
            }}
          >
            <input
              aria-label="Message JARVIS"
              autoComplete="off"
              maxLength={32000}
              placeholder="Speak, or type here"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
            />
            <button type="submit" aria-label="Send message" disabled={!draft.trim()}>
              Send
            </button>
          </form>
        ) : null}

        {view.approval ? (
          <section
            className="approval"
            role="alertdialog"
            aria-modal="false"
            aria-labelledby="approval-title"
            aria-describedby="approval-summary"
          >
            <div>
              <span className="eyebrow">External action</span>
              <h2 id="approval-title">Approval required</h2>
              <p id="approval-summary">{view.approval.summary}</p>
            </div>
            <div className="approval__actions">
              <button
                className="button button--quiet"
                type="button"
                onClick={() => onReject(view.approval?.id ?? "")}
              >
                Reject {action}
              </button>
              <button
                className="button button--approve"
                type="button"
                onClick={() => onApprove(view.approval?.id ?? "")}
              >
                Approve {action}
              </button>
            </div>
          </section>
        ) : null}

        {view.state === "speaking" || view.state === "thinking" || view.state === "acting" ? (
          <button className="interrupt" type="button" onClick={onInterrupt}>
            Interrupt
          </button>
        ) : null}
      </div>
      {surface === "desktop" ? (
        <div className="transit-orb" aria-hidden="true">
          <span className="transit-orb__wake" />
          <span className="transit-orb__orbit transit-orb__orbit--outer" />
          <span className="transit-orb__orbit transit-orb__orbit--inner" />
          <span className="transit-orb__core" />
        </div>
      ) : null}
    </section>
  );
}

const STATE_LABELS: Record<OverlayView["state"], string> = {
  idle: "Ready",
  listening: "Listening",
  transcribing: "Heard you",
  thinking: "Working through it",
  acting: "Carrying that out",
  awaiting_approval: "Waiting for your approval",
  speaking: "Speaking",
  private: "Private mode",
  meeting: "Meeting mode",
  lecture: "Lecture mode",
  ambient: "Ambient memory mode",
  unavailable: "Unavailable",
};

import "./overlay.css";
