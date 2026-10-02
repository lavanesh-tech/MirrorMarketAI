import type { Metadata } from "next";

import { AskPanel } from "@/components/ask-panel";

export const metadata: Metadata = { title: "Ask" };

export default async function Page({ params }: { params: Promise<{ workspaceId: string }> }) {
  const { workspaceId } = await params;
  return <AskPanel workspaceId={workspaceId} />;
}
