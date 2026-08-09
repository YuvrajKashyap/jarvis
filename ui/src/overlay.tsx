import { useState } from "react";

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
  detail: string | null;
};

type ConversationOverlayProps = {
  surface: "desktop" | "phone";
  view: OverlayView;
  onApprove: (approvalId: string) => void;
  onReject: (approvalId: string) => void;
  onInterrupt: () => void;
  onSubmit: (text: string) => void;
  onActivate: () => void;
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
    onApprove,
    onReject,
    onInterrupt,
    onSubmit,
    onActivate,
    pairing,
    onPairPhone,
    onClosePairing,
  } = _props;
  const [draft, setDraft] = useState("");

  if (surface === "phone" && view.connection === "unavailable") {
    return (
      <main className="phone-shell">
        <section className="unavailable-card" aria-describedby="unavailable-detail">
          <span className="eyebrow">Host status</span>
          <h1>JARVIS unavailable</h1>
          <p id="unavailable-detail">Your laptop host is offline or unreachable.</p>
          <div className="horizon horizon--quiet" aria-hidden="true" />
        </section>
      </main>
    );
  }

  const action = view.approval?.capability.split(".").at(-1) ?? "action";
  const stateLabel = STATE_LABELS[view.state];

  return (
    <section className={`overlay overlay--${surface}`} aria-label="JARVIS conversation">
      <header className="status-row">
        <span className="wordmark" aria-hidden="true">
          JARVIS
        </span>
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

      {surface === "desktop" && view.state === "idle" && !pairing ? (
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

      {view.transcript.length > 0 ? (
        <ol className="transcript" aria-label="Conversation transcript">
          {view.transcript.map((line) => (
            <li className={`utterance utterance--${line.speaker}`} key={line.id}>
              <span className="speaker">{line.speaker === "user" ? "You" : "JARVIS"}</span>
              <p>{line.text}</p>
            </li>
          ))}
        </ol>
      ) : null}

      {view.state === "listening" ? (
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
