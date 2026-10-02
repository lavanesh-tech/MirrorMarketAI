"use client";

import Link from "next/link";

import { useMembers, useRequirements, useWorkspace, useWorkspaceProducts } from "@/lib/api/queries";
import { formatDate } from "@/lib/format";

import { ActivityFeed } from "./activity-feed";
import { Discussion } from "./discussion";
import { roleLabel } from "./workspace-list";

/** Where the decision stands, and who is deciding. */
export function WorkspaceOverview({ workspaceId }: { workspaceId: string }) {
  const workspace = useWorkspace(workspaceId);
  const members = useMembers(workspaceId);
  const requirements = useRequirements(workspaceId);
  const products = useWorkspaceProducts(workspaceId);
  const base = `/workspaces/${workspaceId}`;

  const criteria = requirements.data?.current.spec.criteria?.length ?? 0;
  const steps = [
    {
      href: `${base}/requirements`,
      title: "Requirements",
      state: requirements.isPending
        ? "Loading…"
        : requirements.data
          ? `Version ${requirements.data.current_version}, ${criteria} ${criteria === 1 ? "criterion" : "criteria"}`
          : "Not written yet",
    },
    {
      href: `${base}/products`,
      title: "Products",
      state: products.isPending ? "Loading…" : `${products.data?.length ?? 0} in this workspace`,
    },
    {
      href: `${base}/compare`,
      title: "Compare",
      state: "Score the products against the requirements",
    },
  ];

  return (
    <div className="space-y-10">
      <section aria-labelledby="steps-title">
        <h2 id="steps-title" className="mb-3 text-xl">
          This decision
        </h2>
        <ol className="max-w-xl divide-y divide-line border-y border-line">
          {steps.map((step) => (
            <li key={step.title} className="flex justify-between gap-4 py-2.5">
              <Link href={step.href as `/workspaces/${string}`} className="link font-semibold">
                {step.title}
              </Link>
              <span className="text-right text-muted">{step.state}</span>
            </li>
          ))}
        </ol>
        {workspace.data ? (
          <p className="mt-3 text-sm text-muted">
            Created {formatDate(workspace.data.created_at)}.
          </p>
        ) : null}
      </section>
      <Discussion workspaceId={workspaceId} />
      <ActivityFeed workspaceId={workspaceId} />
      <section aria-labelledby="members-title">
        <h2 id="members-title" className="mb-3 text-xl">
          Members
        </h2>
        {members.data ? (
          <ul className="max-w-xl divide-y divide-line border-y border-line">
            {members.data.map((member) => (
              <li key={member.user_id} className="flex justify-between gap-4 py-2.5">
                <span>
                  <span className="font-semibold">{member.display_name}</span>{" "}
                  <span className="text-muted">{member.email}</span>
                </span>
                <span>{roleLabel(member.role)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-muted">
            {members.isError ? "Members could not be loaded." : "Loading members…"}
          </p>
        )}
      </section>
    </div>
  );
}
