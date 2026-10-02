import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { mockApi, renderAt, signIn } from "../test/utils.jsx";
import ClaimPage from "./ClaimPage.jsx";

const CLAIM = {
  id: "c1",
  project_id: "p1",
  repository_id: "r1",
  statement: "P95 latency under 200 ms",
  spec: { type: "LATENCY_P95_LTE", max_ms: 200 },
  status: "SUPPORTED",
  proof_level: "L4",
  evaluated_at: "2026-09-27T12:05:00Z",
  created_at: "2026-09-27T12:00:00Z",
};

describe("ClaimPage", () => {
  it("shows status, reasons, evidence integrity and the timeline", async () => {
    signIn();
    mockApi({
      "GET /claims/c1": CLAIM,
      "GET /claims/c1/timeline": {
        claim_id: "c1",
        events: [
          { at: "2026-09-27T12:00:00Z", kind: "claim_created", ref_id: "c1", details: {} },
          {
            at: "2026-09-27T12:05:00Z",
            kind: "evaluated",
            ref_id: "e1",
            details: {
              status: "SUPPORTED",
              proof_level: "L4",
              changed: true,
              previous_status: "NOT_VERIFIED",
            },
          },
        ],
      },
      "GET /claims/c1/evaluations": [
        {
          id: "e1",
          status: "SUPPORTED",
          proof_level: "L4",
          reasons: ["latency_p95_ms=143.2 meets lte 200"],
          commit_sha: "a".repeat(40),
          evaluator_version: "1",
          created_at: "2026-09-27T12:05:00Z",
        },
      ],
      "GET /claims/c1/evidence-packages": [
        {
          id: "k1",
          commit_sha: "a".repeat(40),
          content_hash: "b".repeat(64),
          integrity_verified: true,
          created_at: "2026-09-27T12:04:00Z",
        },
      ],
    });
    renderAt("/claims/c1", <ClaimPage />, "/claims/:claimId");
    expect(await screen.findByRole("heading", { name: CLAIM.statement })).toBeInTheDocument();
    expect(await screen.findByText("latency_p95_ms=143.2 meets lte 200")).toBeInTheDocument();
    expect(screen.getByText("verified")).toBeInTheDocument();
    const timeline = screen.getByRole("list", { name: "Timeline" });
    expect(within(timeline).getByText("Claim created")).toBeInTheDocument();
    expect(within(timeline).getByText("(was Not verified)")).toBeInTheDocument();
  });

  it("shows a not-found message for a hidden claim", async () => {
    signIn();
    mockApi({});
    renderAt("/claims/zzz", <ClaimPage />, "/claims/:claimId");
    expect(await screen.findByRole("alert")).toHaveTextContent("Not found");
  });
});
