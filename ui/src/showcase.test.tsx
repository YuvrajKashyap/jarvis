import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Showcase, showcaseScenario } from "./showcase";

describe("recruiter showcase", () => {
  it("uses the real conversation overlay for the grounded desktop scenario", () => {
    render(<Showcase scenario="conversation" />);

    expect(screen.getByLabelText("JARVIS conversation")).toBeVisible();
    expect(
      screen.getByText(/Your test runner is open with one failing policy test/i),
    ).toBeVisible();
    expect(screen.getByRole("textbox", { name: "Message JARVIS" })).toBeVisible();
  });

  it("shows the measured hardware boundary without claiming product readiness", () => {
    render(<Showcase scenario="readiness" />);

    expect(screen.getByText("Not daily-ready")).toBeVisible();
    fireEvent.click(screen.getByText("3 checks need attention"));
    expect(screen.getByText(/0.66 GiB remained after loading Qwen3.5 4B Q4/i)).toBeVisible();
  });

  it("parses only known showcase scenarios", () => {
    expect(showcaseScenario("?scenario=phone")).toBe("phone");
    expect(showcaseScenario("?scenario=unknown")).toBe("conversation");
  });
});
