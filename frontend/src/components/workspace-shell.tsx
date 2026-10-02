"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { ApiError } from "@/lib/api/client";
import { useMembers, useWorkspace } from "@/lib/api/queries";
import { RealtimeProvider, useRealtime } from "@/lib/realtime";

import { roleLabel } from "./workspace-list";

const SECTIONS = [
  { slug: "", label: "Overview" },
  { slug: "/requirements", label: "Requirements" },
  { slug: "/products", label: "Products" },
  { slug: "/evidence", label: "Evidence" },
  { slug: "/compare", label: "Compare" },
  { slug: "/ask", label: "Ask" },
] as const;

/** The frame every workspace page shares: its name, your role and the section links. */
export function WorkspaceShell({
  workspaceId,
  children,
}: {
  workspaceId: string;
  children: ReactNode;
}) {
  const workspace = useWorkspace(workspaceId);
  const pathname = usePathname();
  const base = `/workspaces/${workspaceId}`;

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
    <RealtimeProvider workspaceId={workspaceId}>
      <Link href="/workspaces" className="link text-sm">
        All workspaces
      </Link>
      <h1 className="mt-2 text-title">{workspace.data.name}</h1>
      <p className="mt-1 text-muted">
        You are {roleLabel(workspace.data.my_role).toLowerCase()} here.
      </p>
      <Presence workspaceId={workspaceId} />
      <nav aria-label="Workspace sections" className="mt-6 border-b-2 border-ink">
        <ul className="flex flex-wrap gap-x-1">
          {SECTIONS.map((section) => {
            const href = `${base}${section.slug}`;
            const current = pathname === href;
            return (
              <li key={section.label}>
                <Link
                  href={href as `/workspaces/${string}`}
                  aria-current={current ? "page" : undefined}
                  className={`inline-block px-3 py-2 font-semibold ${
                    current ? "bg-ink text-paper" : "hover:bg-surface"
                  }`}
                >
                  {section.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
      <div className="pt-8">{children}</div>
    </RealtimeProvider>
  );
}

/** Who else has this workspace open, and whether the page is updating by itself. */
function Presence({ workspaceId }: { workspaceId: string }) {
  const { status, online } = useRealtime();
  const members = useMembers(workspaceId);
  const names = online
    .map((id) => members.data?.find((member) => member.user_id === id)?.display_name)
    .filter((name): name is string => Boolean(name));
  return (
    <p className="mt-1 text-sm text-muted" aria-live="polite">
      {status === "live"
        ? `Live. Here now: ${names.length > 0 ? names.join(", ") : "just you"}.`
        : status === "connecting"
          ? "Connecting for live updates…"
          : "Live updates are off. Reload to see what others changed."}
    </p>
  );
}
