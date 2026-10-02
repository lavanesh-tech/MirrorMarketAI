import type { ReactNode } from "react";

import { EvidenceSpecimen } from "@/components/evidence-specimen";
import { Wordmark } from "@/components/wordmark";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-h-dvh lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
      <section className="hidden flex-col justify-between border-r border-line p-12 lg:flex">
        <Wordmark />
        <div>
          <p className="mb-10 max-w-md font-[family-name:var(--font-display)] text-display font-semibold tracking-tight">
            Decide together. Every claim shows its source.
          </p>
          <EvidenceSpecimen />
        </div>
        <p className="text-sm text-muted">
          Verdicts, prices and rankings are computed, not guessed.
        </p>
      </section>
      <main className="flex flex-col justify-center bg-surface px-6 py-12 sm:px-12">
        <div className="mx-auto w-full max-w-sm">
          <div className="mb-10 lg:hidden">
            <Wordmark />
          </div>
          {children}
        </div>
      </main>
    </div>
  );
}
