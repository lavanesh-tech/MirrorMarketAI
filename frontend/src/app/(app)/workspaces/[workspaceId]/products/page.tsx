import type { Metadata } from "next";

import { ProductsPanel } from "@/components/products-panel";

export const metadata: Metadata = { title: "Products" };

export default async function Page({ params }: { params: Promise<{ workspaceId: string }> }) {
  const { workspaceId } = await params;
  return <ProductsPanel workspaceId={workspaceId} />;
}
