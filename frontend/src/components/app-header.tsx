"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Wordmark } from "@/components/wordmark";
import { postSession } from "@/lib/api/client";
import { useMe } from "@/lib/api/queries";

export function AppHeader() {
  const me = useMe();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [leaving, setLeaving] = useState(false);

  async function logOut() {
    setLeaving(true);
    await postSession("/api/session/logout").catch(() => undefined);
    queryClient.clear(); // nothing of this user's data stays in memory
    router.replace("/login");
    router.refresh();
  }

  return (
    <header className="border-b border-line bg-surface">
      <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-6 py-3">
        <Link href="/workspaces" aria-label="MirrorMarket, your workspaces">
          <Wordmark />
        </Link>
        <div className="flex items-center gap-4">
          {me.data ? <span className="text-muted">{me.data.display_name}</span> : null}
          <button type="button" className="link" onClick={logOut} disabled={leaving}>
            {leaving ? "Logging out…" : "Log out"}
          </button>
        </div>
      </div>
    </header>
  );
}
