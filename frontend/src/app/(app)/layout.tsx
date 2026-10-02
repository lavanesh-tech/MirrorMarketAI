import type { ReactNode } from "react";

import { AppHeader } from "@/components/app-header";

export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <a
        href="#content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:bg-surface focus:px-3 focus:py-2"
      >
        Skip to content
      </a>
      <AppHeader />
      <main id="content" className="mx-auto max-w-5xl px-6 py-10">
        {children}
      </main>
    </>
  );
}
