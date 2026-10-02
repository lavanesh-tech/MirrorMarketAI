import type { Metadata } from "next";

import { EvidenceSearch } from "@/components/evidence-search";

export const metadata: Metadata = { title: "Evidence" };

export default async function Page({ params }: { params: Promise<{ workspaceId: string }> }) {
  const { workspaceId } = await params;
  return <EvidenceSearch workspaceId={workspaceId} />;
}
