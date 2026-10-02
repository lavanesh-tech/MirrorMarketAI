"use client";

import { useState, type FormEvent } from "react";

import { ApiError } from "@/lib/api/client";
import { useEvidenceSearch, useWorkspaceProducts } from "@/lib/api/queries";

const AUTHORITY_WORDS: Record<string, string> = {
  OFFICIAL: "Official",
  THIRD_PARTY: "Third party",
  USER: "Added by a member",
};

/** Search the passages the agents can cite, to check what the sources really say. */
export function EvidenceSearch({ workspaceId }: { workspaceId: string }) {
  const products = useWorkspaceProducts(workspaceId);
  const search = useEvidenceSearch(workspaceId);
  const [error, setError] = useState<string | null>(null);

  const productName = (id: string) => {
    const found = products.data?.find((item) => item.product.id === id)?.product;
    return found ? `${found.brand} ${found.name}` : "A product no longer in this workspace";
  };

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const query = String(data.get("q") ?? "").trim();
    if (query === "") {
      setError("Enter something to search for.");
      return;
    }
    setError(null);
    const productId = String(data.get("product_id") ?? "");
    try {
      await search.mutateAsync({ query, productIds: productId === "" ? [] : [productId] });
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "The search could not be run.");
    }
  }

  const result = search.data;
  return (
    <div className="space-y-8">
      <section aria-labelledby="evidence-title">
        <h2 id="evidence-title" className="mb-1 text-xl">
          Search the sources
        </h2>
        <p className="mb-3 max-w-xl text-muted">
          Finds passages by meaning and by exact words, across every source added to this
          workspace&apos;s products.
        </p>
        <form onSubmit={onSubmit} role="search" noValidate className="max-w-3xl">
          <div className="grid gap-3 sm:grid-cols-[3fr_2fr_auto] sm:items-end">
            <div>
              <label htmlFor="evidence-q" className="block text-sm font-semibold">
                Search for
              </label>
              <input
                id="evidence-q"
                name="q"
                type="search"
                className="field"
                maxLength={500}
                placeholder="battery life"
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? "evidence-error" : undefined}
              />
            </div>
            <div>
              <label htmlFor="evidence-product" className="block text-sm font-semibold">
                In
              </label>
              <select id="evidence-product" name="product_id" className="field" defaultValue="">
                <option value="">All products</option>
                {(products.data ?? []).map(({ product }) => (
                  <option key={product.id} value={product.id}>
                    {product.brand} {product.name}
                  </option>
                ))}
              </select>
            </div>
            <button type="submit" className="button" disabled={search.isPending}>
              {search.isPending ? "Searching…" : "Search"}
            </button>
          </div>
          {error ? (
            <p id="evidence-error" role="alert" className="mt-2 text-sm font-medium text-unmet">
              {error}
            </p>
          ) : null}
        </form>
      </section>

      {result ? (
        <section aria-labelledby="results-title">
          <h2 id="results-title" className="mb-1 text-xl">
            {result.items.length === 0
              ? `Nothing found for “${result.query}”`
              : `${result.items.length} ${result.items.length === 1 ? "passage" : "passages"} for “${result.query}”`}
          </h2>
          {result.degraded ? (
            <p className="mb-3 text-sm">
              Meaning-based search was unavailable, so only exact words were matched.
            </p>
          ) : null}
          {result.items.length === 0 ? (
            <p className="max-w-xl text-muted">
              Try other words, or add sources on the Products page.
            </p>
          ) : (
            <ol className="max-w-3xl divide-y divide-line border-y border-line">
              {result.items.map((hit) => (
                <li key={hit.chunk_id} className="py-3">
                  <p className="text-sm text-muted">
                    <span className="font-semibold text-ink">{productName(hit.product_id)}</span> ·{" "}
                    {hit.source_url ? (
                      <a
                        href={hit.source_url}
                        className="link"
                        rel="noreferrer noopener"
                        target="_blank"
                      >
                        {hit.source_title}
                      </a>
                    ) : (
                      hit.source_title
                    )}{" "}
                    · {AUTHORITY_WORDS[hit.authority] ?? hit.authority}
                  </p>
                  <blockquote className="mt-1 border-l-4 border-mark pl-3 whitespace-pre-wrap">
                    {hit.text}
                  </blockquote>
                </li>
              ))}
            </ol>
          )}
        </section>
      ) : null}
    </div>
  );
}
