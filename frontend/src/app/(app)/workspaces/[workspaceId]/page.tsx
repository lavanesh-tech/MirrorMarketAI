import type { Metadata } from "next";

import { WorkspaceOverview } from "@/components/workspace-overview";

export const metadata: Metadata = { title: "Workspace" };

export default async function WorkspacePage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = await params;
  return <WorkspaceOverview workspaceId={workspaceId} />;
}
