"use client";

import { useState, type FormEvent } from "react";

import { ApiError } from "@/lib/api/client";
import { useMe, useProduct, useSaveSpecifications } from "@/lib/api/queries";
import { criterionLabel, formatMoney, trimNumber } from "@/lib/requirements";
import { specificationSchema } from "@/lib/validation";

type Props = {
  productId: string;
  productName: string;
  /** Keys worth filling in: the workspace's criteria, plus the price. */
  suggestedKeys: string[];
};

/** A product's specifications: what the comparison falls back on when no source says otherwise. */
export function ProductSpecs({ productId, productName, suggestedKeys }: Props) {
  const product = useProduct(productId);
  const me = useMe();
  const save = useSaveSpecifications(productId);
  const [error, setError] = useState<string | null>(null);

  if (product.isPending) return <p className="text-muted">Loading specifications…</p>;
  if (product.isError) {
    return <p role="alert">The specifications could not be loaded.</p>;
  }

  const specs = product.data.specifications.filter((spec) => spec.variant_id === null);
  const missing = suggestedKeys.filter((key) => !specs.some((spec) => spec.key === key));
  // The catalog is shared by everyone, so only the person who added a product edits it.
  const editable = me.data?.id === product.data.created_by_id;
  const id = `spec-${productId}`;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const parsed = specificationSchema.safeParse(Object.fromEntries(new FormData(form)));
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message ?? "Check the specification.");
      return;
    }
    setError(null);
    try {
      await save.mutateAsync([parsed.data]);
      form.reset();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "The specification could not be saved.");
    }
  }

  return (
    <div className="space-y-4">
      {specs.length > 0 ? (
        <table className="w-full max-w-xl border-collapse text-left">
          <caption className="sr-only">Specifications of {productName}</caption>
          <tbody>
            {specs.map((spec) => (
              <tr key={spec.key} className="border-b border-line">
                <th scope="row" className="py-1.5 pr-4 font-normal text-muted">
                  {criterionLabel(spec.key)}
                </th>
                <td className="py-1.5 font-semibold">
                  {spec.key === "price" && spec.value_number != null
                    ? formatMoney(spec.value_number, (spec.unit ?? "USD").toUpperCase())
                    : `${spec.value_number != null ? trimNumber(spec.value_number) : spec.value_text}${
                        spec.unit ? ` ${spec.unit}` : ""
                      }`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="text-muted">No specifications yet.</p>
      )}
      {missing.length > 0 ? (
        <p className="max-w-xl text-sm">
          Missing for your requirements: {missing.map((key) => criterionLabel(key)).join(", ")}.
        </p>
      ) : null}
      {editable ? (
        <form onSubmit={onSubmit} noValidate className="max-w-2xl">
          <div className="grid gap-3 sm:grid-cols-[2fr_2fr_1fr_auto] sm:items-end">
            <div>
              <label htmlFor={`${id}-key`} className="block text-sm font-semibold">
                Specification
              </label>
              <input
                id={`${id}-key`}
                name="key"
                className="field"
                list={`${id}-keys`}
                autoComplete="off"
                placeholder="ram_gb"
                aria-describedby={error ? `${id}-error` : undefined}
              />
              <datalist id={`${id}-keys`}>
                {suggestedKeys.map((key) => (
                  <option key={key} value={key}>
                    {criterionLabel(key)}
                  </option>
                ))}
              </datalist>
            </div>
            <div>
              <label htmlFor={`${id}-value`} className="block text-sm font-semibold">
                Value
              </label>
              <input id={`${id}-value`} name="value" className="field" placeholder="16" />
            </div>
            <div>
              <label htmlFor={`${id}-unit`} className="block text-sm font-semibold">
                Unit
              </label>
              <input id={`${id}-unit`} name="unit" className="field" placeholder="GB" />
            </div>
            <button type="submit" className="button-quiet" disabled={save.isPending}>
              {save.isPending ? "Saving…" : "Save"}
            </button>
          </div>
          {error ? (
            <p id={`${id}-error`} role="alert" className="mt-2 text-sm font-medium text-unmet">
              {error}
            </p>
          ) : (
            <p className="mt-2 text-sm text-muted">
              Saving a specification that already exists replaces its value. For a price, use
              “price” with the currency as the unit.
            </p>
          )}
        </form>
      ) : (
        <p className="max-w-xl text-sm text-muted">
          Only the person who added this product to the catalog can change its specifications.
        </p>
      )}
    </div>
  );
}
