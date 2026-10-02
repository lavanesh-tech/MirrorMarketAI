"use client";

import { useVote, useVotes } from "@/lib/api/queries";

/** Thumbs up / down for one product. Pressing your current vote again removes it. */
export function VoteButtons({
  workspaceId,
  productId,
  productName,
  canVote,
}: {
  workspaceId: string;
  productId: string;
  productName: string;
  canVote: boolean;
}) {
  const votes = useVotes(workspaceId);
  const vote = useVote(workspaceId);
  const tally = votes.data?.find((item) => item.product_id === productId);
  const mine = tally?.my_vote ?? 0;

  const button = (value: 1 | -1, label: string, count: number) => (
    <button
      type="button"
      className={`border-[1.5px] border-ink px-2 py-0.5 text-sm font-semibold tabular-nums ${
        mine === value ? "bg-ink text-paper" : "hover:bg-surface"
      }`}
      aria-pressed={mine === value}
      aria-label={`${label} ${productName}, ${count} so far`}
      disabled={!canVote || vote.isPending}
      onClick={() => vote.mutate({ productId, value: mine === value ? 0 : value })}
    >
      {value === 1 ? "For" : "Against"} {count}
    </button>
  );

  return (
    <span className="inline-flex gap-1.5">
      {button(1, "Vote for", tally?.up ?? 0)}
      {button(-1, "Vote against", tally?.down ?? 0)}
    </span>
  );
}
