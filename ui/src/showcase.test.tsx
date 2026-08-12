import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Showcase, showcaseScenario } from "./showcase";

describe("reproducible interface media", () => {
  it("shows screen context, personal schedule, and resource policy in one conversation", () => {
    render(<Showcase scenario="conversation" />);

    expect(screen.getByLabelText("JARVIS conversation")).toBeVisible();
    expect(screen.getByText(/Can I leave this running while I head to practice/i)).toBeVisible();
    expect(screen.getByText(/Tennis starts at 4:30/i)).toBeVisible();
    expect(screen.getByText(/keep the model unloaded afterward/i)).toBeVisible();
    expect(screen.getByRole("textbox", { name: "Message JARVIS" })).toBeVisible();
  });

  it("shows a restrained proactive suggestion tied to an authorized project watch", () => {
    render(<Showcase scenario="proactivity" />);

    expect(screen.getByText("Permission regression in the latest build")).toBeVisible();
    expect(screen.getByText("308 passed · 63 passed · 20 passed")).toBeVisible();
    expect(screen.getByText(/passed on main 27 minutes ago/i)).toBeVisible();
    fireEvent.click(screen.getByText("Why I mentioned it"));
    expect(screen.getByText(/watch JARVIS builds while you are coding/i)).toBeVisible();
  });

  it("shows the exact external action instead of generic approval copy", () => {
    render(<Showcase scenario="approval" />);

    expect(screen.getByText(/Send the prepared update to Maya Chen/i)).toBeVisible();
    expect(screen.queryByText(/recruiter@example.com/i)).not.toBeInTheDocument();
  });

  it("continues the same objective on the phone", () => {
    render(<Showcase scenario="phone" />);

    expect(screen.getByText(/Picked up from your desktop/i)).toBeVisible();
    expect(screen.getByText(/24 GB and 32 GB VRAM hosts/i)).toBeVisible();
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
