"use client";

import Link from "next/link";

import { ApiError } from "@/lib/api/client";
import { useMembers, useWorkspace } from "@/lib/api/queries";
import { formatDate } from "@/lib/format";

import { roleLabel } from "./workspace-list";

export function WorkspaceOverview({ workspaceId }: { workspaceId: string }) {
  const workspace = useWorkspace(workspaceId);
  const members = useMembers(workspaceId);

  if (workspace.isPending) return <p className="text-muted">Loading the workspace…</p>;
  if (workspace.isError) {
    const missing = workspace.error instanceof ApiError && workspace.error.status === 404;
    return (
      <div>
        <h1 className="text-title">
          {missing ? "Workspace not found" : "This workspace could not be loaded"}
        </h1>
        <p className="mt-2 text-muted">
          {missing
            ? "It may have been removed, or you are not a member of it."
            : "Check your connection and load the page again."}
        </p>
        <Link href="/workspaces" className="link mt-6 inline-block">
          Back to your workspaces
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-10">
      <div>
        <Link href="/workspaces" className="link text-sm">
          All workspaces
        </Link>
        <h1 className="mt-2 text-title">{workspace.data.name}</h1>
        <p className="mt-2 text-muted">
          You are {roleLabel(workspace.data.my_role).toLowerCase()} here. Created{" "}
          {formatDate(workspace.data.created_at)}.
        </p>
      </div>
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
