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

  it("shows proactivity as an interface preview instead of a fabricated event", () => {
    render(<Showcase scenario="proactivity" />);

    expect(screen.getByText("Proactive assistance")).toBeVisible();
    expect(screen.getByText(/Authorized event suggestions appear here/i)).toBeVisible();
    fireEvent.click(screen.getByText("Why I mentioned it"));
    expect(
      screen.getByText(/only appears for events you have allowed JARVIS to monitor/i),
    ).toBeVisible();
  });

  it("labels the approval state as a non-operational interface preview", () => {
    render(<Showcase scenario="approval" />);

    expect(screen.getByText(/No message will be sent/i)).toBeVisible();
    expect(screen.queryByRole("list", { name: "Conversation transcript" })).not.toBeInTheDocument();
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
    expect(showcaseScenario("?scenario=phone")).toBe("phone");
    expect(showcaseScenario("?scenario=unknown")).toBe("conversation");
  });

  it("contains no marketing captions inside the captured product surface", () => {
    render(<Showcase scenario="conversation" />);

    expect(screen.queryByText("LOCAL WINDOWS OVERLAY")).not.toBeInTheDocument();
    expect(screen.queryByText("EVIDENCE-BASED READINESS")).not.toBeInTheDocument();
  });
});
