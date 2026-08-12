import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Showcase, showcaseScenario } from "./showcase";

describe("reproducible interface media", () => {
  it("renders only the JARVIS surface without a fabricated host application", () => {
    const { container } = render(<Showcase scenario="conversation" />);

    expect(screen.getByLabelText("JARVIS conversation")).toBeVisible();
    expect(container.querySelector(".showcase")?.children).toHaveLength(1);
    expect(container.querySelector(".workbench")).not.toBeInTheDocument();
    expect(container.querySelector(".mail")).not.toBeInTheDocument();
    expect(screen.queryByText("evaluate_models.py")).not.toBeInTheDocument();
    expect(screen.queryByText("Maya Chen")).not.toBeInTheDocument();
  });

  it("shows the actual ready conversation surface without a fabricated exchange", () => {
    const { container } = render(<Showcase scenario="conversation" />);
    const scope = within(container);

    expect(scope.getByRole("status")).toHaveTextContent("Ready");
    expect(scope.getByRole("textbox", { name: "Message JARVIS" })).toBeVisible();
    expect(scope.queryByRole("list", { name: "Conversation transcript" })).not.toBeInTheDocument();
  });

  it("shows a useful proactive resource intervention grounded in a measured event", () => {
    render(<Showcase scenario="proactivity" />);

    expect(screen.getByText("The local model crossed its memory limit")).toBeVisible();
    expect(screen.getByText(/below the 1 GiB safety floor/i)).toBeVisible();
    expect(screen.getByText(/No applications were closed/i)).toBeVisible();
    expect(screen.getByRole("button", { name: "Show measurements" })).toBeVisible();
    fireEvent.click(screen.getByText("Why I mentioned it"));
    expect(screen.getByText(/protect normal desktop headroom/i)).toBeVisible();
  });

  it("shows a useful memory example grounded in the measured JARVIS model test", () => {
    render(<Showcase scenario="memory" />);

    expect(screen.getByText(/What did we learn from the 9B model test/i)).toBeVisible();
    expect(screen.getByText(/87.8 seconds/i)).toBeVisible();
    expect(screen.getByText(/219 MB of available system memory/i)).toBeVisible();
  });

  it("shows a useful private-mode example with the audio behavior made explicit", () => {
    const rendered = render(<Showcase scenario="privacy" />);
    const scope = within(rendered.container);

    expect(scope.getByRole("status")).toHaveTextContent("Private mode");
    expect(scope.getByText(/Go completely private while I take this call/i)).toBeVisible();
    expect(scope.getByText(/rolling audio buffer is disabled/i)).toBeVisible();
  });

  it("shows the reusable resting orb without an overlay shell", () => {
    const { container } = render(<Showcase scenario="orb" />);

    expect(screen.getByRole("button", { name: "Open JARVIS" })).toBeVisible();
    expect(container.querySelector(".overlay")).not.toBeInTheDocument();
  });

  it("labels the approval state as a non-operational interface preview", () => {
    const rendered = render(<Showcase scenario="approval" />);
    const scope = within(rendered.container);

    expect(scope.getByText(/No message will be sent/i)).toBeVisible();
    expect(scope.queryByRole("list", { name: "Conversation transcript" })).not.toBeInTheDocument();
  });

  it("shows the real empty phone companion state", () => {
    const { container } = render(<Showcase scenario="phone" />);
    const scope = within(container);

    expect(scope.getByRole("button", { name: "Talk to JARVIS" })).toBeVisible();
    expect(scope.getByText("Keep JARVIS one tap away")).toBeVisible();
    expect(scope.queryByRole("list", { name: "Conversation transcript" })).not.toBeInTheDocument();
  });

  it("parses only known showcase scenarios", () => {
    expect(showcaseScenario("?scenario=proactivity")).toBe("proactivity");
    expect(showcaseScenario("?scenario=memory")).toBe("memory");
    expect(showcaseScenario("?scenario=privacy")).toBe("privacy");
    expect(showcaseScenario("?scenario=orb")).toBe("orb");
    expect(showcaseScenario("?scenario=phone")).toBe("phone");
    expect(showcaseScenario("?scenario=unknown")).toBe("conversation");
  });

  it("contains no marketing captions inside the captured product surface", () => {
    render(<Showcase scenario="conversation" />);

    expect(screen.queryByText("LOCAL WINDOWS OVERLAY")).not.toBeInTheDocument();
    expect(screen.queryByText("EVIDENCE-BASED READINESS")).not.toBeInTheDocument();
  });
});
