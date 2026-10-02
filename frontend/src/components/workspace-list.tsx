"use client";

import Link from "next/link";

import { useWorkspaces } from "@/lib/api/queries";
import { formatDate } from "@/lib/format";

export function WorkspaceList() {
  const workspaces = useWorkspaces();

  if (workspaces.isPending) {
    return <p className="text-muted">Loading your workspaces…</p>;
  }
  if (workspaces.isError) {
    return (
      <div role="alert" className="border-l-4 border-unmet bg-surface px-4 py-3">
        <p className="font-medium">Your workspaces could not be loaded.</p>
        <button type="button" className="link mt-1" onClick={() => workspaces.refetch()}>
          Try again
        </button>
      </div>
    );
  }
  const items = workspaces.data.items;
  if (items.length === 0) {
    return (
      <p className="max-w-xl text-muted">
        No workspaces yet. Create one above, then add the products you are choosing between.
      </p>
    );
  }
  return (
    <table className="w-full border-collapse text-left">
      <caption className="sr-only">Your workspaces</caption>
      <thead>
        <tr className="border-b-2 border-ink text-sm">
          <th scope="col" className="py-2 pr-4 font-semibold">
            Workspace
          </th>
          <th scope="col" className="py-2 pr-4 font-semibold">
            Your role
          </th>
          <th scope="col" className="py-2 font-semibold">
            Created
          </th>
        </tr>
      </thead>
      <tbody>
        {items.map((workspace) => (
          <tr key={workspace.id} className="border-b border-line">
            <th scope="row" className="py-3 pr-4 font-semibold">
              <Link href={`/workspaces/${workspace.id}`} className="link">
                {workspace.name}
              </Link>
            </th>
            <td className="py-3 pr-4">{roleLabel(workspace.my_role)}</td>
            <td className="py-3 text-muted">{formatDate(workspace.created_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const ROLE_LABELS: Record<string, string> = {
  OWNER: "Owner",
  EDITOR: "Editor",
  MEMBER: "Member",
  VIEWER: "Viewer",
};

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role;
}
