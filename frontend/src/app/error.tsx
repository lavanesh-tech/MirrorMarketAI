"use client";

export default function ErrorPage({ reset }: { error: Error; reset: () => void }) {
  return (
    <main className="mx-auto max-w-xl px-6 py-24">
      <h1 className="text-title">This page failed to load</h1>
      <p className="mt-3 text-muted">Nothing was lost. Loading it again usually works.</p>
      <button type="button" className="button mt-6" onClick={reset}>
        Load again
      </button>
    </main>
  );
}
