import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConversationOverlay, type OverlayView } from "./overlay";

function view(overrides: Partial<OverlayView> = {}): OverlayView {
  return {
    connection: "connected",
    state: "listening",
    transcript: [],
    approval: null,
    detail: null,
    ...overrides,
  };
}

describe("ConversationOverlay", () => {
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
    render(
      <ConversationOverlay
        surface="phone"
        view={view({ connection: "unavailable", state: "unavailable" })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onInterrupt={vi.fn()}
        onSubmit={vi.fn()}
        onActivate={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "JARVIS unavailable" })).toBeVisible();
    expect(screen.getByText("Your laptop host is offline or unreachable.")).toBeVisible();
    expect(
      screen.queryByText(/apple intelligence|siri|fallback assistant/i),
    ).not.toBeInTheDocument();
  });

  it("shows a content-protected one-use QR pairing card only on desktop", () => {
    const onClosePairing = vi.fn();
    render(
      <ConversationOverlay
        surface="desktop"
        view={view({ state: "idle" })}
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
    fireEvent.click(screen.getByRole("button", { name: "Close pairing" }));
    expect(onClosePairing).toHaveBeenCalledOnce();
  });
});
