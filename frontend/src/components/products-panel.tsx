"use client";

import { useState, type FormEvent } from "react";

import { ApiError } from "@/lib/api/client";
import {
  useAddWorkspaceProduct,
  useCatalogSearch,
  useCreateProduct,
  useRemoveWorkspaceProduct,
  useRequirements,
  useWorkspace,
  useWorkspaceProducts,
} from "@/lib/api/queries";
import { formatDate } from "@/lib/format";
import { canEdit, canRun } from "@/lib/requirements";
import { CATEGORIES, fieldErrors, productSchema, type FieldErrors } from "@/lib/validation";

import { PriceHistoryView } from "./price-history";
import { ProductSources } from "./product-sources";
import { ProductSpecs } from "./product-specs";
import { VoteButtons } from "./vote-buttons";

const SECTIONS = [
  ["specs", "Specifications"],
  ["sources", "Sources"],
  ["prices", "Prices"],
] as const;
type Section = (typeof SECTIONS)[number][0];

export function ProductsPanel({ workspaceId }: { workspaceId: string }) {
  const workspace = useWorkspace(workspaceId);
  const products = useWorkspaceProducts(workspaceId);
  const requirements = useRequirements(workspaceId);
  const remove = useRemoveWorkspaceProduct(workspaceId);
  const [open, setOpen] = useState<{ productId: string; section: Section } | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (products.isPending) return <p className="text-muted">Loading the products…</p>;
  if (products.isError) {
    return (
      <div role="alert" className="border-l-4 border-unmet bg-surface px-4 py-3">
        <p className="font-medium">The products could not be loaded.</p>
        <button type="button" className="link mt-1" onClick={() => products.refetch()}>
          Try again
        </button>
      </div>
    );
  }

  const editable = workspace.data ? canEdit(workspace.data.my_role) : false;
  const participant = workspace.data ? canRun(workspace.data.my_role) : false;
  const spec = requirements.data?.current.spec;
  const suggestedKeys = [
    ...new Set([...(spec?.criteria ?? []).map((c) => c.key), ...(spec?.budget ? ["price"] : [])]),
  ];
  const added = new Set(products.data.map((item) => item.product.id));

  async function onRemove(productId: string) {
    setError(null);
    try {
      await remove.mutateAsync(productId);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "The product could not be removed.");
    }
  }

  return (
    <div className="space-y-10">
      <section aria-labelledby="products-title">
        <h2 id="products-title" className="mb-3 text-xl">
          Products being compared
        </h2>
        {error ? (
          <p role="alert" className="mb-3 font-medium text-unmet">
            {error}
          </p>
        ) : null}
        {products.data.length === 0 ? (
          <p className="max-w-xl text-muted">
            {editable
              ? "None yet. Add the products you are choosing between below."
              : "None yet. An owner or editor of this workspace can add them."}
          </p>
        ) : (
          <ul className="divide-y divide-line border-y border-line">
            {products.data.map(({ product, added_at }) => {
              const name = `${product.brand} ${product.name}`;
              const section = open?.productId === product.id ? open.section : null;
              return (
                <li key={product.id} className="py-3">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
                    <div>
                      <h3 className="font-body text-base font-semibold tracking-normal">{name}</h3>
                      <p className="text-sm text-muted">
                        {product.category}, added {formatDate(added_at)}
                      </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                      <VoteButtons
                        workspaceId={workspaceId}
                        productId={product.id}
                        productName={name}
                        canVote={participant}
                      />
                      {SECTIONS.map(([key, label]) => {
                        const expanded = section === key;
                        return (
                          <button
                            key={key}
                            type="button"
                            className="link"
                            aria-label={`${expanded ? "Hide " + label.toLowerCase() : label} of ${name}`}
                            aria-expanded={expanded}
                            aria-controls={`details-${product.id}`}
                            onClick={() =>
                              setOpen(expanded ? null : { productId: product.id, section: key })
                            }
                          >
                            {expanded ? `Hide ${label.toLowerCase()}` : label}
                          </button>
                        );
                      })}
                      {editable ? (
                        <button
                          type="button"
                          className="link"
                          aria-label={`Remove ${name}`}
                          disabled={remove.isPending}
                          onClick={() => onRemove(product.id)}
                        >
                          Remove
                        </button>
                      ) : null}
                    </div>
                  </div>
                  <div id={`details-${product.id}`} hidden={section === null} className="mt-4">
                    {section === "specs" ? (
                      <ProductSpecs
                        productId={product.id}
                        productName={name}
                        suggestedKeys={suggestedKeys}
                      />
                    ) : section === "sources" ? (
                      <ProductSources
                        workspaceId={workspaceId}
                        productId={product.id}
                        productName={name}
                        editable={editable}
                      />
                    ) : section === "prices" ? (
                      <PriceHistoryView
                        productId={product.id}
                        productName={name}
                        canRecord={participant}
                      />
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>
      {editable ? <AddProduct workspaceId={workspaceId} added={added} /> : null}
    </div>
  );
}

function AddProduct({ workspaceId, added }: { workspaceId: string; added: Set<string> }) {
  const [query, setQuery] = useState("");
  const results = useCatalogSearch(query);
  const add = useAddWorkspaceProduct(workspaceId);
  const create = useCreateProduct();
  const [errors, setErrors] = useState<
    FieldErrors<{ brand: string; name: string; category: string }>
  >({});
  const [formError, setFormError] = useState<string | null>(null);

  function onSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setQuery(String(new FormData(event.currentTarget).get("q") ?? "").trim());
  }

  async function onAdd(productId: string) {
    setFormError(null);
    try {
      await add.mutateAsync(productId);
    } catch (cause) {
      setFormError(cause instanceof ApiError ? cause.message : "The product could not be added.");
    }
  }

  async function onCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const parsed = productSchema.safeParse(Object.fromEntries(new FormData(form)));
    if (!parsed.success) {
      setErrors(fieldErrors(parsed.error));
      return;
    }
    setErrors({});
    setFormError(null);
    try {
      const product = await create.mutateAsync(parsed.data);
      await add.mutateAsync(product.id);
      form.reset();
    } catch (cause) {
      setFormError(
        cause instanceof ApiError && cause.status === 409
          ? "That product is already in the catalog. Search for it above and add it from there."
          : cause instanceof ApiError
            ? cause.message
            : "The product could not be added.",
      );
    }
  }

  return (
    <section aria-labelledby="add-title">
      <h2 id="add-title" className="mb-3 text-xl">
        Add a product
      </h2>
      {formError ? (
        <p role="alert" className="mb-3 font-medium text-unmet">
          {formError}
        </p>
      ) : null}
      <form onSubmit={onSearch} role="search" className="flex max-w-xl gap-3">
        <div className="grow">
          <label htmlFor="catalog-search" className="sr-only">
            Search the catalog
          </label>
          <input
            id="catalog-search"
            name="q"
            type="search"
            className="field"
            maxLength={100}
            placeholder="Brand or product name"
          />
        </div>
        <button type="submit" className="button shrink-0">
          Search the catalog
        </button>
      </form>
      {query === "" ? null : results.isPending ? (
        <p className="mt-3 text-muted">Searching…</p>
      ) : results.isError ? (
        <p role="alert" className="mt-3">
          The catalog could not be searched.
        </p>
      ) : results.data.length === 0 ? (
        <p className="mt-3 text-muted">Nothing in the catalog matches “{query}”.</p>
      ) : (
        <ul
          aria-label="Search results"
          className="mt-3 max-w-xl divide-y divide-line border-y border-line"
        >
          {results.data.map((product) => (
            <li key={product.id} className="flex items-baseline justify-between gap-4 py-2.5">
              <span>
                <span className="font-semibold">
                  {product.brand} {product.name}
                </span>{" "}
                <span className="text-muted">{product.category}</span>
              </span>
              {added.has(product.id) ? (
                <span className="text-muted">Added</span>
              ) : (
                <button
                  type="button"
                  className="link"
                  aria-label={`Add ${product.brand} ${product.name}`}
                  disabled={add.isPending}
                  onClick={() => onAdd(product.id)}
                >
                  Add
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      <h3 className="mt-8 mb-1 text-lg">Not in the catalog?</h3>
      <p className="mb-3 max-w-xl text-muted">
        Add it once and everyone can compare it. You can fill in its specifications afterwards.
      </p>
      <form onSubmit={onCreate} noValidate className="grid max-w-2xl gap-3 sm:grid-cols-3">
        <NewProductField name="brand" label="Brand" error={errors.brand} />
        <NewProductField name="name" label="Product name" error={errors.name} />
        <div>
          <label htmlFor="new-category" className="block font-semibold">
            Category
          </label>
          <select
            id="new-category"
            name="category"
            className="field"
            defaultValue=""
            aria-invalid={errors.category ? true : undefined}
            aria-describedby={errors.category ? "new-category-error" : undefined}
          >
            <option value="" disabled>
              Choose…
            </option>
            {CATEGORIES.map((category) => (
              <option key={category} value={category}>
                {category}
              </option>
            ))}
          </select>
          {errors.category ? (
            <p id="new-category-error" className="mt-1 text-sm font-medium text-unmet">
              {errors.category}
            </p>
          ) : null}
        </div>
        <div className="sm:col-span-3">
          <button type="submit" className="button" disabled={create.isPending || add.isPending}>
            {create.isPending || add.isPending ? "Adding…" : "Add to catalog and workspace"}
          </button>
        </div>
      </form>
    </section>
  );
}

function NewProductField({ name, label, error }: { name: string; label: string; error?: string }) {
  const id = `new-${name}`;
  return (
    <div>
      <label htmlFor={id} className="block font-semibold">
        {label}
      </label>
      <input
        id={id}
        name={name}
        className="field"
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : undefined}
      />
      {error ? (
        <p id={`${id}-error`} className="mt-1 text-sm font-medium text-unmet">
          {error}
        </p>
      ) : null}
    </div>
  );
}
