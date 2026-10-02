"use client";

import { STEP_WORDS, agentLabel, engineLabel, finding, parseOrchestration } from "@/lib/agents";
import { useLatestRun } from "@/lib/api/queries";
import { formatDateTime } from "@/lib/format";

const VERDICT_WORDS: Record<string, string> = {
  RECOMMENDED: "Recommended",
  CONSIDER: "Worth considering",
  NOT_RECOMMENDED: "Not recommended",
  INSUFFICIENT_DATA: "Not enough information",
};

const STEP_STYLE = { SUCCEEDED: "text-met", FAILED: "text-unmet", SKIPPED: "text-muted" } as const;

/** What the last research run did for each product: every agent step and the verdict. */
export function AgentStatus({ workspaceId }: { workspaceId: string }) {
  const latest = useLatestRun(workspaceId, "orchestration");
  const run = latest.data;
  const report = run ? parseOrchestration(run.output) : null;
  if (!run || !report) return null;

  return (
    <section aria-labelledby="research-title" className="max-w-3xl">
      <h2 id="research-title" className="mb-1 text-xl">
        How the products were researched
      </h2>
      <p className="mb-4 text-sm text-muted">
        Last run {formatDateTime(run.created_at)}, {(report.elapsed_ms / 1000).toFixed(1)} seconds.
        {report.time_budget_exhausted ? " It ran out of time, so some steps were skipped." : ""}
        {report.token_budget_exhausted
          ? " It reached its AI usage limit and finished with the built-in rules."
          : ""}
        {report.products_skipped.length > 0
          ? ` ${report.products_skipped.length} more products were over the per-run limit.`
          : ""}
      </p>
      <div className="space-y-6">
        {report.products.map((product) => (
          <article key={product.product_id} aria-label={product.synthesis?.product_name}>
            <h3 className="font-body text-base font-semibold tracking-normal">
              {product.synthesis?.product_name ?? "Product"}
              {product.synthesis ? (
                <span className="font-normal text-muted">
                  {" "}
                  · {VERDICT_WORDS[product.synthesis.verdict] ?? product.synthesis.verdict}
                </span>
              ) : null}
            </h3>
            {product.synthesis ? <p className="mt-1">{product.synthesis.summary}</p> : null}
            {product.synthesis?.blockers.length ? (
              <p className="mt-1 text-sm">
                <span className="font-semibold text-unmet">Blockers:</span>{" "}
                {product.synthesis.blockers.map(finding).join("; ")}
              </p>
            ) : null}
            {product.synthesis?.concerns.length ? (
              <p className="mt-1 text-sm">
                <span className="font-semibold">Concerns:</span>{" "}
                {product.synthesis.concerns.map(finding).join("; ")}
              </p>
            ) : null}
            <ul className="mt-2 divide-y divide-line border-y border-line text-sm">
              {product.steps.map((step) => (
                <li key={step.agent} className="flex justify-between gap-4 py-1.5">
                  <span>{agentLabel(step.agent)}</span>
                  <span>
                    <span className={`font-semibold ${STEP_STYLE[step.status]}`}>
                      {STEP_WORDS[step.status]}
                    </span>
                    <span className="text-muted">
                      {step.status === "SUCCEEDED"
                        ? ` · ${engineLabel(step.engine)} · ${step.duration_ms} ms`
                        : step.reason
                          ? ` · ${step.reason}`
                          : ""}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          </article>
        ))}
      </div>
    </section>
  );
}
