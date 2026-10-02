import type { Metadata } from "next";

import { ComparisonPanel } from "@/components/comparison-panel";

export const metadata: Metadata = { title: "Compare" };

export default async function Page({ params }: { params: Promise<{ workspaceId: string }> }) {
  const { workspaceId } = await params;
  return <ComparisonPanel workspaceId={workspaceId} />;
}
