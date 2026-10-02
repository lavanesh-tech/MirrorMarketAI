"use client";

import { useState, type FormEvent } from "react";

import { ApiError } from "@/lib/api/client";
import { useAddSource, useSources, type NewSource } from "@/lib/api/queries";
import { formatDate } from "@/lib/format";
import { SOURCE_TYPES, textSourceSchema, urlSourceSchema } from "@/lib/validation";

type Kind = NewSource["kind"];

const STATUS_WORDS: Record<string, string> = {
  PENDING: "Not read yet",
  INGESTED: "Read",
  FAILED: "Could not be read",
};

const typeLabel = (value: string) =>
  SOURCE_TYPES.find(([type]) => type === value)?.[1] ?? value.replaceAll("_", " ").toLowerCase();

/** The documents the agents may cite for one product, and a form to add more. */
export function ProductSources({
  workspaceId,
  productId,
  productName,
  editable,
}: {
  workspaceId: string;
  productId: string;
  productName: string;
  editable: boolean;
}) {
  const sources = useSources(productId);
  const add = useAddSource(workspaceId, productId);
  const [kind, setKind] = useState<Kind>("text");
  const [error, setError] = useState<string | null>(null);
  const id = `source-${productId}`;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const base = { title: data.get("title"), sourceType: data.get("sourceType") };
    let input: NewSource;
    if (kind === "file") {
      const file = data.get("file");
      const parsed = textSourceSchema.pick({ title: true, sourceType: true }).safeParse(base);
      if (!parsed.success || !(file instanceof File) || file.size === 0) {
        setError(
          parsed.success
            ? "Choose a file."
            : (parsed.error.issues[0]?.message ?? "Check the form."),
        );
        return;
      }
      input = { kind, ...parsed.data, file };
    } else if (kind === "url") {
      const parsed = urlSourceSchema.safeParse({ ...base, url: data.get("url") });
      if (!parsed.success) {
        setError(parsed.error.issues[0]?.message ?? "Check the form.");
        return;
      }
      input = { kind, ...parsed.data };
    } else {
      const parsed = textSourceSchema.safeParse({ ...base, text: data.get("text") });
      if (!parsed.success) {
        setError(parsed.error.issues[0]?.message ?? "Check the form.");
        return;
      }
      input = { kind, ...parsed.data };
    }
    setError(null);
    try {
      await add.mutateAsync(input);
      form.reset();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "The source could not be added.");
    }
  }

  return (
    <div className="space-y-4">
      {sources.isPending ? (
        <p className="text-muted">Loading sources…</p>
      ) : sources.isError ? (
        <p role="alert">The sources could not be loaded.</p>
      ) : sources.data.length === 0 ? (
        <p className="max-w-xl text-muted">
          No sources yet. Without sources, research can only use the catalog specifications and
          nothing can be cited.
        </p>
      ) : (
        <ul aria-label={`Sources for ${productName}`} className="max-w-2xl divide-y divide-line">
          {sources.data.map((source) => (
            <li key={source.id} className="py-2">
              <span className="font-semibold">
                {source.url ? (
                  <a href={source.url} className="link" rel="noreferrer noopener" target="_blank">
                    {source.title}
                  </a>
                ) : (
                  source.title
                )}
              </span>
              <span className="block text-sm text-muted">
                {typeLabel(source.source_type)} · {STATUS_WORDS[source.status] ?? source.status}
                {source.workspace_id ? " · only this workspace" : " · shared"} · added{" "}
                {formatDate(source.created_at)}
              </span>
              {source.last_error ? (
                <span className="block text-sm text-unmet">{source.last_error}</span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {editable ? (
        <form onSubmit={onSubmit} noValidate className="max-w-2xl space-y-3">
          <fieldset>
            <legend className="font-semibold">Add a source</legend>
            <div className="mt-1 flex flex-wrap gap-x-5 gap-y-1">
              {(
                [
                  ["text", "Paste text"],
                  ["file", "Upload a file"],
                  ["url", "Web address"],
                ] as const
              ).map(([value, label]) => (
                <label key={value} className="flex items-center gap-1.5">
                  <input
                    type="radio"
                    name="kind"
                    value={value}
                    checked={kind === value}
                    onChange={() => setKind(value)}
                  />
                  {label}
                </label>
              ))}
            </div>
          </fieldset>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor={`${id}-title`} className="block text-sm font-semibold">
                Title
              </label>
              <input id={`${id}-title`} name="title" className="field" maxLength={300} />
            </div>
            <div>
              <label htmlFor={`${id}-type`} className="block text-sm font-semibold">
                Kind of source
              </label>
              <select
                id={`${id}-type`}
                name="sourceType"
                className="field"
                defaultValue="SPECIFICATION_SHEET"
              >
                {SOURCE_TYPES.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {kind === "text" ? (
            <div>
              <label htmlFor={`${id}-text`} className="block text-sm font-semibold">
                Text
              </label>
              <textarea id={`${id}-text`} name="text" className="field min-h-28" />
            </div>
          ) : kind === "file" ? (
            <div>
              <label htmlFor={`${id}-file`} className="block text-sm font-semibold">
                File (PDF, HTML, Markdown or text)
              </label>
              <input
                id={`${id}-file`}
                name="file"
                type="file"
                className="field"
                accept=".pdf,.html,.htm,.md,.txt,application/pdf,text/html,text/markdown,text/plain"
              />
            </div>
          ) : (
            <div>
              <label htmlFor={`${id}-url`} className="block text-sm font-semibold">
                Web address
              </label>
              <input
                id={`${id}-url`}
                name="url"
                type="url"
                className="field"
                placeholder="https://"
              />
            </div>
          )}
          {error ? (
            <p role="alert" className="text-sm font-medium text-unmet">
              {error}
            </p>
          ) : null}
          <button type="submit" className="button-quiet" disabled={add.isPending}>
            {add.isPending ? "Reading the source…" : "Add source"}
          </button>
        </form>
      ) : null}
    </div>
  );
}
