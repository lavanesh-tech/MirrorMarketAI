"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { ApiError } from "@/lib/api/client";
import {
  useCompare,
  useLatestComparison,
  useRequirements,
  useWorkspace,
  useWorkspaceProducts,
} from "@/lib/api/queries";
import { MAX_WEIGHT, parseComparison, weightChanges, type Comparison } from "@/lib/comparison";
import { formatDate } from "@/lib/format";
import { canRun } from "@/lib/requirements";

import { ComparisonMatrix } from "./comparison-matrix";

export function ComparisonPanel({ workspaceId }: { workspaceId: string }) {
  const workspace = useWorkspace(workspaceId);
  const requirements = useRequirements(workspaceId);
  const products = useWorkspaceProducts(workspaceId);
  const latest = useLatestComparison(workspaceId);
  const compare = useCompare(workspaceId);
  const [error, setError] = useState<string | null>(null);
  const base = `/workspaces/${workspaceId}`;

  if (requirements.isPending || products.isPending || latest.isPending) {
    return <p className="text-muted">Loading the comparison…</p>;
  }
  if (requirements.isError || products.isError || latest.isError) {
    return (
      <div role="alert" className="border-l-4 border-unmet bg-surface px-4 py-3">
        <p className="font-medium">The comparison could not be loaded.</p>
        <button
          type="button"
          className="link mt-1"
          onClick={() => {
            void requirements.refetch();
            void products.refetch();
            void latest.refetch();
          }}
        >
          Try again
        </button>
      </div>
    );
  }

  const spec = requirements.data?.current.spec;
  const hasTargets = Boolean(spec && ((spec.criteria?.length ?? 0) > 0 || spec.budget));
  if (!hasTargets || products.data.length === 0) {
    return (
      <div className="max-w-xl">
        <h2 className="mb-2 text-xl">Not ready to compare yet</h2>
        <p className="text-muted">A comparison needs both of these:</p>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          <li>
            <Link href={`${base}/requirements` as `/workspaces/${string}`} className="link">
              Requirements
            </Link>{" "}
            with a budget or at least one criterion{hasTargets ? " (done)" : ""}
          </li>
          <li>
            <Link href={`${base}/products` as `/workspaces/${string}`} className="link">
              Products
            </Link>{" "}
            to choose between{products.data.length > 0 ? " (done)" : ""}
          </li>
        </ul>
      </div>
    );
  }

  const run = latest.data;
  const comparison = run ? parseComparison(run.output) : null;
  const runnable = workspace.data ? canRun(workspace.data.my_role) : false;
  const stale = run !== null && run.requirement_version !== requirements.data?.current_version;
  const units = Object.fromEntries(
    (spec?.criteria ?? []).flatMap((c) => (c.unit ? [[c.key, c.unit] as const] : [])),
  );

  async function start(research: boolean, weights: Record<string, number>, budgetIsHard: boolean) {
    setError(null);
    try {
      await compare.mutateAsync({ research, weights, budgetIsHard });
    } catch (cause) {
      setError(
        cause instanceof ApiError && cause.code === "nothing_to_compare"
          ? "There is nothing to compare yet. Add requirements and products first."
          : cause instanceof ApiError
            ? cause.message
            : "The comparison could not be made.",
      );
    }
  }

  return (
    <div className="space-y-8">
      {error ? (
        <p role="alert" className="border-l-4 border-unmet bg-surface px-4 py-3 font-medium">
          {error}
        </p>
      ) : null}

      {comparison && run ? (
        <>
          <Verdict comparison={comparison} />
          {stale ? (
            <p role="status" className="border-l-4 border-ink bg-surface px-4 py-3">
              The requirements changed after this comparison (it used version{" "}
              {run.requirement_version}). Research and compare again to bring it up to date.
            </p>
          ) : null}
          <ComparisonMatrix
            comparison={comparison}
            units={units}
            currency={spec?.budget?.currency ?? "USD"}
          />
          <p className="max-w-3xl text-sm text-muted">
            <span className="mark text-ink">Highlighted</span> values come from a cited source.
            Other values come from the catalog specifications. Compared {formatDate(run.created_at)}
            .
          </p>
        </>
      ) : (
        <div className="max-w-xl">
          <h2 className="mb-2 text-xl">No comparison yet</h2>
          <p className="text-muted">
            Each product is researched for every requirement, then scored. Must-haves rule products
            out; everything else adds points by weight.
          </p>
        </div>
      )}

      {runnable ? (
        <section aria-labelledby="run-title" className="max-w-3xl">
          <h2 id="run-title" className="mb-3 text-xl">
            {comparison ? "Adjust and compare again" : "Make the comparison"}
          </h2>
          {/* Keyed by run, so the weight inputs restart from each new comparison. */}
          <Controls
            key={run?.id ?? "first"}
            comparison={comparison}
            pending={compare.isPending}
            onRun={start}
          />
        </section>
      ) : (
        <p className="text-muted">Viewers can read comparisons but not run them.</p>
      )}
    </div>
  );
}

function Verdict({ comparison }: { comparison: Comparison }) {
  const winner = comparison.products.find((p) => p.product_id === comparison.winner_product_id);
  const changes = weightChanges(comparison);
  return (
    <section aria-labelledby="verdict-title" className="max-w-3xl">
      <h2 id="verdict-title" className="text-title">
        {winner ? (
          <>
            <span className="mark">{winner.name}</span> is the best match
          </>
        ) : (
          "No clear best match"
        )}
      </h2>
      <p className="mt-2">
        {winner
          ? comparison.margin !== null
            ? `It leads by ${comparison.margin.toFixed(1)} points. `
            : "It is the only product that meets the must-haves. "
          : "No product has every must-have confirmed. Fill in the missing specifications or relax a must-have. "}
        {winner
          ? comparison.sensitivity.stable
            ? "Halving or doubling the importance of any one requirement does not change the result."
            : `The result is sensitive: a different product wins if you ${changes.join(", or ")}.`
          : null}
      </p>
    </section>
  );
}

function Controls({
  comparison,
  pending,
  onRun,
}: {
  comparison: Comparison | null;
  pending: boolean;
  onRun: (research: boolean, weights: Record<string, number>, budgetIsHard: boolean) => void;
}) {
  const criteria = comparison?.criteria ?? [];
  const price = criteria.find((c) => c.key === "price");
  const [weights, setWeights] = useState<Record<string, number>>(
    Object.fromEntries(criteria.map((c) => [c.key, c.weight])),
  );
  const [budgetIsHard, setBudgetIsHard] = useState(price ? price.hard : true);

  // Enter in an importance field rescores when there is research to reuse.
  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onRun(comparison === null, weights, budgetIsHard);
  }

  return (
    <form onSubmit={onSubmit} className="space-y-5">
      {criteria.length > 0 ? (
        <fieldset>
          <legend className="font-semibold">Importance</legend>
          <p className="mb-2 text-sm text-muted">
            0 ignores a requirement, {MAX_WEIGHT} makes it count the most.
          </p>
          <div className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
            {criteria.map((criterion) => (
              <div key={criterion.key} className="flex items-center justify-between gap-3">
                <label htmlFor={`weight-${criterion.key}`}>{criterion.label}</label>
                <input
                  id={`weight-${criterion.key}`}
                  type="number"
                  className="field w-24 py-1.5 tabular-nums"
                  min={0}
                  max={MAX_WEIGHT}
                  step={0.5}
                  value={weights[criterion.key] ?? criterion.weight}
                  onChange={(event) =>
                    setWeights({
                      ...weights,
                      [criterion.key]: Math.min(
                        MAX_WEIGHT,
                        Math.max(0, Number(event.target.value) || 0),
                      ),
                    })
                  }
                />
              </div>
            ))}
          </div>
        </fieldset>
      ) : null}
      <label className="flex items-start gap-2">
        <input
          type="checkbox"
          className="mt-1 size-4"
          checked={budgetIsHard}
          onChange={(event) => setBudgetIsHard(event.target.checked)}
        />
        <span>
          Treat the budget as a must-have
          <span className="block text-sm text-muted">
            Products over budget are ruled out instead of just losing points.
          </span>
        </span>
      </label>
      <div className="flex flex-wrap gap-3">
        <button
          type="button"
          className="button"
          disabled={pending}
          onClick={() => onRun(true, weights, budgetIsHard)}
        >
          {pending ? "Working…" : "Research and compare"}
        </button>
        {comparison ? (
          <button type="submit" className="button-quiet" disabled={pending}>
            Rescore with these settings
          </button>
        ) : null}
      </div>
      <p className="text-sm text-muted" aria-live="polite">
        {pending
          ? "Researching each product can take a minute."
          : comparison
            ? "Research looks up every product again. Rescoring reuses the last research."
            : null}
      </p>
    </form>
  );
}
