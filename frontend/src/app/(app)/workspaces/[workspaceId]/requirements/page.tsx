import type { Metadata } from "next";

import { RequirementsPanel } from "@/components/requirements-panel";

export const metadata: Metadata = { title: "Requirements" };

export default async function Page({ params }: { params: Promise<{ workspaceId: string }> }) {
  const { workspaceId } = await params;
  return <RequirementsPanel workspaceId={workspaceId} />;
}
