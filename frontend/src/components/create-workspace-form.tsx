"use client";

import { useRef, useState, type FormEvent } from "react";

import { ApiError } from "@/lib/api/client";
import { useCreateWorkspace } from "@/lib/api/queries";
import { workspaceSchema } from "@/lib/validation";

export function CreateWorkspaceForm() {
  const create = useCreateWorkspace();
  const [error, setError] = useState<string | null>(null);
  // Kept across retries of the same submission; replaced after a success.
  const idempotencyKey = useRef(crypto.randomUUID());

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const parsed = workspaceSchema.safeParse(Object.fromEntries(new FormData(form)));
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message ?? "Check the name.");
      return;
    }
    setError(null);
    try {
      await create.mutateAsync({ name: parsed.data.name, idempotencyKey: idempotencyKey.current });
      idempotencyKey.current = crypto.randomUUID();
      form.reset();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not create the workspace.");
    }
  }

  return (
    <form onSubmit={onSubmit} noValidate className="max-w-xl">
      <label htmlFor="workspace-name" className="block font-semibold">
        New workspace
      </label>
      <p id="workspace-name-hint" className="mb-2 text-sm text-muted">
        Name it after the decision, for example “Laptop for college”.
      </p>
      <div className="flex gap-3">
        <input
          id="workspace-name"
          name="name"
          className="field"
          maxLength={120}
          aria-invalid={error ? true : undefined}
          aria-describedby={`workspace-name-hint${error ? " workspace-name-error" : ""}`}
        />
        <button type="submit" className="button shrink-0" disabled={create.isPending}>
          {create.isPending ? "Creating…" : "Create workspace"}
        </button>
      </div>
      {error ? (
        <p id="workspace-name-error" role="alert" className="mt-2 text-sm font-medium text-unmet">
          {error}
        </p>
      ) : null}
    </form>
  );
}
