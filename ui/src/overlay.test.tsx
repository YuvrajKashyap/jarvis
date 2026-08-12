import { readFileSync } from "node:fs";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConversationOverlay, type OverlayView } from "./overlay";

function view(overrides: Partial<OverlayView> = {}): OverlayView {
  return {
    connection: "connected",
    state: "listening",
    transcript: [],
    approval: null,
    suggestion: null,
    detail: null,
    ...overrides,
  };
}

describe("ConversationOverlay", () => {
  it("plainly surfaces blocked and unverified product readiness", () => {
    const rendered = render(
      <ConversationOverlay
        surface="desktop"
        view={view({ state: "idle" })}
        readiness={{
          overall: "blocked",
          generated_at: "2026-08-11T15:30:00Z",
          checks: [
            {
              code: "model_quality",
              state: "unverified",
              summary: "The selected model has not passed the JARVIS quality suite.",
              detail: null,
            },
            {
              code: "resources",
              state: "blocked",
              summary: "Current resource pressure prevents a safe model workload.",
              detail: "0.2 GiB available; GPU 78 C.",
            },
          ],
        }}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onInterrupt={vi.fn()}
        onSubmit={vi.fn()}
        onActivate={vi.fn()}
      />,
    );

    expect(screen.getByText("Not daily-ready")).toBeVisible();
    fireEvent.click(screen.getByText("2 checks need attention"));
    expect(screen.getByText(/selected model has not passed/i)).toBeVisible();
    expect(screen.getByText(/resource pressure prevents/i)).toBeVisible();
    expect(screen.getByText("0.2 GiB available; GPU 78 C.")).toBeVisible();
    rendered.unmount();
  });

  it("offers a proactive thought without implying that JARVIS acted", () => {
    const onSubmit = vi.fn();
    const onDismissSuggestion = vi.fn();
    const rendered = render(
      <ConversationOverlay
        surface="desktop"
        view={view({
          state: "idle",
          suggestion: {
            id: "suggestion-1",
            title: "You have been deep in this for a while",
            message: "I can help you checkpoint the work before the context gets expensive.",
            reason: "The same application has stayed in the foreground for 71 minutes.",
            suggestedPrompt: "Help me checkpoint what I am working on.",
            priority: "quiet",
          },
        })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onInterrupt={vi.fn()}
        onSubmit={onSubmit}
        onActivate={vi.fn()}
        onDismissSuggestion={onDismissSuggestion}
      />,
    );

    const scope = within(rendered.container);
    expect(scope.getByRole("region", { name: "JARVIS suggestion" })).toBeVisible();
    expect(scope.getByText("Why I mentioned it")).toBeVisible();
    expect(scope.queryByText(/I fixed|I changed|I closed/i)).not.toBeInTheDocument();
    fireEvent.click(scope.getByRole("button", { name: "Talk it through" }));
    expect(onSubmit).toHaveBeenCalledWith("Help me checkpoint what I am working on.");
    expect(onDismissSuggestion).toHaveBeenCalledTimes(1);
    rendered.unmount();
  });

  it("lets a quiet suggestion disappear without starting a conversation", () => {
    const onSubmit = vi.fn();
    const onDismissSuggestion = vi.fn();
    const rendered = render(
      <ConversationOverlay
        surface="desktop"
        view={view({
          state: "idle",
          suggestion: {
            id: "suggestion-1",
            title: "A download just finished",
            message: "I can help you decide what to do with it.",
            reason: "A new download finished and stopped changing.",
            suggestedPrompt: "Help me inspect the new download.",
            priority: "quiet",
          },
        })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onInterrupt={vi.fn()}
        onSubmit={onSubmit}
        onActivate={vi.fn()}
        onDismissSuggestion={onDismissSuggestion}
      />,
    );

    fireEvent.click(within(rendered.container).getByRole("button", { name: "Not now" }));
    expect(onDismissSuggestion).toHaveBeenCalledTimes(1);
    expect(onSubmit).not.toHaveBeenCalled();
    rendered.unmount();
  });

  it("shows immediate listening feedback without generic filler", () => {
    const onSubmit = vi.fn();
    render(
      <ConversationOverlay
        surface="desktop"
        view={view()}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onInterrupt={vi.fn()}
        onSubmit={onSubmit}
        onActivate={vi.fn()}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("Listening");
    expect(screen.queryByText(/one second|let me check|please wait/i)).not.toBeInTheDocument();
    expect(screen.getByLabelText("JARVIS is listening")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Message JARVIS" }), {
      target: { value: "What am I looking at?" },
    });
    fireEvent.submit(screen.getByRole("form", { name: "Message JARVIS" }));
    expect(onSubmit).toHaveBeenCalledWith("What am I looking at?");
  });

  it("renders user and assistant transcript as plain text", () => {
    render(
      <ConversationOverlay
        surface="desktop"
        view={view({
          state: "speaking",
          transcript: [
            { id: "1", speaker: "user", text: "What am I looking at?", isFinal: true },
            {
              id: "2",
              speaker: "assistant",
              text: "Your test runner is open with one failing policy test.",
              isFinal: true,
            },
          ],
        })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onInterrupt={vi.fn()}
        onSubmit={vi.fn()}
        onActivate={vi.fn()}
      />,
    );

    expect(screen.getByText("What am I looking at?")).toBeInTheDocument();
    expect(
      screen.getByText("Your test runner is open with one failing policy test."),
    ).toBeInTheDocument();
  });

  it("keeps the desktop composer available after an assistant response completes", () => {
    const onSubmit = vi.fn();
    const rendered = render(
      <ConversationOverlay
        surface="desktop"
        view={view({
          state: "idle",
          transcript: [
            { id: "1", speaker: "user", text: "Hello?", isFinal: true },
            { id: "2", speaker: "assistant", text: "I'm here.", isFinal: true },
          ],
        })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onInterrupt={vi.fn()}
        onSubmit={onSubmit}
        onActivate={vi.fn()}
      />,
    );

    const scope = within(rendered.container);
    fireEvent.change(scope.getByRole("textbox", { name: "Message JARVIS" }), {
      target: { value: "Good. What were we discussing?" },
    });
    fireEvent.submit(scope.getByRole("form", { name: "Message JARVIS" }));

    expect(onSubmit).toHaveBeenCalledWith("Good. What were we discussing?");
  });

  it("keeps phone pairing out of an established conversation", () => {
    const rendered = render(
      <ConversationOverlay
        surface="desktop"
        view={view({
          state: "idle",
          transcript: [
            { id: "1", speaker: "user", text: "Hello?", isFinal: true },
            { id: "2", speaker: "assistant", text: "I'm here.", isFinal: true },
          ],
        })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onInterrupt={vi.fn()}
        onSubmit={vi.fn()}
        onActivate={vi.fn()}
        onPairPhone={vi.fn()}
      />,
    );

    expect(
      within(rendered.container).queryByRole("button", { name: "Pair iPhone" }),
    ).not.toBeInTheDocument();
  });

  it("gives the paired iPhone exact Home Screen and Action Button setup guidance", () => {
    const rendered = render(
      <ConversationOverlay
        surface="phone"
        view={view({ state: "idle" })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onInterrupt={vi.fn()}
        onSubmit={vi.fn()}
        onActivate={vi.fn()}
      />,
    );

    const scope = within(rendered.container);
    fireEvent.click(scope.getByText("Keep JARVIS one tap away"));
    expect(scope.getByText(/Share, then Add to Home Screen/i)).toBeVisible();
    expect(scope.getByText(/Action Button/i)).toBeVisible();
    expect(scope.getByText(/Siri/i)).toBeVisible();
  });

  it("keeps the newest exchange visible as the conversation grows", () => {
    const rendered = render(
      <ConversationOverlay
        surface="desktop"
        view={view({
          transcript: [{ id: "1", speaker: "user", text: "First turn", isFinal: true }],
        })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onInterrupt={vi.fn()}
        onSubmit={vi.fn()}
        onActivate={vi.fn()}
      />,
    );
    const transcript = within(rendered.container).getByRole("list", {
      name: "Conversation transcript",
    });
    Object.defineProperty(transcript, "scrollHeight", { configurable: true, value: 420 });

    rendered.rerender(
      <ConversationOverlay
        surface="desktop"
        view={view({
          transcript: [
            { id: "1", speaker: "user", text: "First turn", isFinal: true },
            {
              id: "2",
              speaker: "assistant",
              text: "The newest response must remain visible.",
              isFinal: true,
            },
          ],
        })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onInterrupt={vi.fn()}
        onSubmit={vi.fn()}
        onActivate={vi.fn()}
      />,
    );

    expect(transcript.scrollTop).toBe(420);
  });

  it("insets the desktop composer from every clipped window edge", () => {
    const rendered = render(
      <ConversationOverlay
        surface="desktop"
        view={view()}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onInterrupt={vi.fn()}
        onSubmit={vi.fn()}
        onActivate={vi.fn()}
      />,
    );

    expect(within(rendered.container).getByRole("form", { name: "Message JARVIS" })).toHaveClass(
      "message-box",
    );
    const stylesheet = readFileSync("ui/src/overlay.css", "utf8");
    expect(stylesheet).toMatch(/\.message-box\s*{[^}]*margin:\s*12px 16px 16px;/s);
  });

  it("renders the desktop glass without a hard perimeter seam", () => {
    const stylesheet = document.createElement("style");
    stylesheet.textContent = readFileSync("ui/src/overlay.css", "utf8");
    document.head.append(stylesheet);
    try {
      const rendered = render(
        <ConversationOverlay
          surface="desktop"
          view={view()}
          onApprove={vi.fn()}
          onReject={vi.fn()}
          onInterrupt={vi.fn()}
          onSubmit={vi.fn()}
          onActivate={vi.fn()}
        />,
      );

      const overlay = within(rendered.container).getByLabelText("JARVIS conversation");
      const style = getComputedStyle(overlay);

      expect(style.borderLeftWidth).toBe("0px");
      expect(style.borderRightWidth).toBe("0px");
      expect(style.boxShadow).toBe("none");
    } finally {
      stylesheet.remove();
    }
  });

  it("lets desktop content define the native overlay height without taking over the screen", () => {
    const stylesheet = readFileSync("ui/src/overlay.css", "utf8");

    expect(stylesheet).toMatch(/\.overlay--desktop\s*{[^}]*height:\s*auto;/s);
    expect(stylesheet).not.toMatch(
      /\.overlay--desktop\s*{[^}]*height:\s*calc\(100vh\s*-\s*\(var\(--surface-inset\)\s*\*\s*2\)\);/s,
    );
    expect(stylesheet).toMatch(
      /\.overlay--desktop\s+\.transcript\s*{[^}]*max-height:\s*280px;[^}]*flex:\s*0\s+1\s+auto;/s,
    );
  });

  it("morphs into a dedicated orb surface during native relocation", () => {
    const stylesheet = readFileSync("ui/src/overlay.css", "utf8");

    expect(stylesheet).toMatch(/\.transit-orb\s*{/);
    expect(stylesheet).toMatch(
      /html\[data-overlay-transit="local"\][^{]*\.transit-orb\s*{[^}]*opacity:\s*1;/s,
    );
    expect(stylesheet).toMatch(
      /html\[data-overlay-transit="cross-monitor"\][^{]*\.overlay__content\s*{[^}]*opacity:\s*0;/s,
    );
    expect(stylesheet).not.toMatch(
      /html\[data-overlay-transit="resize"\][^{]*\.(?:transit-orb|overlay__content)/s,
    );
    expect(stylesheet).toMatch(/prefers-reduced-motion:\s*reduce/);
  });

  it("makes the exact external action explicit before approval", () => {
    const onApprove = vi.fn();
    const onReject = vi.fn();
    render(
      <ConversationOverlay
        surface="desktop"
        view={view({
          state: "awaiting_approval",
          approval: {
            id: "approval-1",
            capability: "messages.send",
            summary: "Send the prepared email to recruiter@example.com",
            risk: "external_irreversible",
          },
        })}
        onApprove={onApprove}
        onReject={onReject}
        onInterrupt={vi.fn()}
        onSubmit={vi.fn()}
        onActivate={vi.fn()}
      />,
    );

    expect(screen.getByRole("alertdialog")).toHaveAccessibleName("Approval required");
    expect(screen.getByText("Send the prepared email to recruiter@example.com")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Approve send" }));
    fireEvent.click(screen.getByRole("button", { name: "Reject send" }));
    expect(onApprove).toHaveBeenCalledWith("approval-1");
    expect(onReject).toHaveBeenCalledWith("approval-1");
  });

  it("shows the explicit unavailable state on phone without another assistant", () => {
    const onRetryConnection = vi.fn();
    const rendered = render(
      <ConversationOverlay
        surface="phone"
        view={view({
          connection: "unavailable",
          state: "unavailable",
          detail: "This pairing code expired. Generate a new code from your laptop.",
        })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onInterrupt={vi.fn()}
        onSubmit={vi.fn()}
        onActivate={vi.fn()}
        onRetryConnection={onRetryConnection}
      />,
    );

    const scope = within(rendered.container);
    expect(scope.getByRole("heading", { name: "JARVIS connection failed" })).toBeVisible();
    expect(
      scope.getByText("This pairing code expired. Generate a new code from your laptop."),
    ).toBeVisible();
    fireEvent.click(scope.getByRole("button", { name: "Try again" }));
    expect(onRetryConnection).toHaveBeenCalledOnce();
    expect(
      scope.queryByText(/apple intelligence|siri|fallback assistant/i),
    ).not.toBeInTheDocument();
  });

  it("shows an honest connection state while phone pairing is still in progress", () => {
    const rendered = render(
      <ConversationOverlay
        surface="phone"
        view={view({ connection: "reconnecting", state: "idle" })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onInterrupt={vi.fn()}
        onSubmit={vi.fn()}
        onActivate={vi.fn()}
      />,
    );

    const scope = within(rendered.container);
    expect(scope.getByRole("heading", { name: "Connecting to JARVIS" })).toBeVisible();
    expect(scope.queryByText("JARVIS unavailable")).not.toBeInTheDocument();
  });

  it("provides an obvious desktop drag affordance without turning actions into drag targets", () => {
    const onMoveOverlay = vi.fn();
    const rendered = render(
      <ConversationOverlay
        surface="desktop"
        view={view({ state: "idle" })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onInterrupt={vi.fn()}
        onSubmit={vi.fn()}
        onActivate={vi.fn()}
        onMoveOverlay={onMoveOverlay}
      />,
    );

    fireEvent.pointerDown(within(rendered.container).getByRole("button", { name: "Move JARVIS" }));
    expect(onMoveOverlay).toHaveBeenCalledOnce();
  });

  it("shows a content-protected one-use QR pairing card only on desktop", () => {
    const onClosePairing = vi.fn();
    render(
      <ConversationOverlay
        surface="desktop"
        view={view({
          state: "idle",
          transcript: [
            { id: "before-pairing", speaker: "assistant", text: "Previous reply", isFinal: true },
          ],
        })}
        pairing={{
          qrDataUrl: "data:image/png;base64,private-qr",
          expiresAt: "2026-08-08T02:05:00Z",
          error: null,
        }}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onInterrupt={vi.fn()}
        onSubmit={vi.fn()}
        onActivate={vi.fn()}
        onPairPhone={vi.fn()}
        onClosePairing={onClosePairing}
      />,
    );

    expect(screen.getByRole("dialog", { name: "Pair iPhone" })).toBeVisible();
    expect(screen.getByRole("img", { name: "One-use iPhone pairing code" })).toBeVisible();
    expect(screen.queryByText("Previous reply")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close pairing" }));
    expect(onClosePairing).toHaveBeenCalledOnce();
  });

  it.each([
    ["meeting", "Meeting mode"],
    ["lecture", "Lecture mode"],
    ["ambient", "Ambient memory mode"],
  ] as const)("shows the explicit %s awareness state", (state, label) => {
    const rendered = render(
      <ConversationOverlay
        surface="desktop"
        view={view({ state })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onInterrupt={vi.fn()}
        onSubmit={vi.fn()}
        onActivate={vi.fn()}
      />,
    );

    expect(within(rendered.container).getByRole("status")).toHaveTextContent(label);
    rendered.unmount();
  });
});
