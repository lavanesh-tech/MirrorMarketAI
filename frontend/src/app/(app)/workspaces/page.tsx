import type { Metadata } from "next";

import { CreateWorkspaceForm } from "@/components/create-workspace-form";
import { WorkspaceList } from "@/components/workspace-list";

export const metadata: Metadata = { title: "Workspaces" };

export default function WorkspacesPage() {
  return (
    <div className="space-y-10">
      <div>
        <h1 className="text-title">Workspaces</h1>
        <p className="mt-2 max-w-xl text-muted">
          A workspace holds one buying decision: what you need, the products you are comparing, and
          the evidence for each.
        </p>
      </div>
      <CreateWorkspaceForm />
      <WorkspaceList />
    </div>
  );
}
