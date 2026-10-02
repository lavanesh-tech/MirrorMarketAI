import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { STATUS_INFO } from "../lib/format.js";
import { ProofLevelBadge, StatusBadge } from "./StatusBadge.jsx";

describe("StatusBadge", () => {
  it.each(Object.entries(STATUS_INFO))("renders %s with an explanation", (status, info) => {
    render(<StatusBadge status={status} />);
    const badge = screen.getByText(info.label);
    expect(badge).toHaveAttribute("title", info.description);
  });

  it("never labels a claim as false", () => {
    for (const info of Object.values(STATUS_INFO)) {
      expect(`${info.label} ${info.description}`.toLowerCase()).not.toMatch(/\bfalse\b/);
    }
  });

  it("shows the proof level with its meaning", () => {
    render(<ProofLevelBadge level="L4" />);
    expect(screen.getByText("L4")).toHaveAttribute("title", expect.stringContaining("Repeated"));
  });
});
