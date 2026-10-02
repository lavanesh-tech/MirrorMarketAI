"use client";

import { useState, type FormEvent } from "react";

import { ApiError } from "@/lib/api/client";
import {
  useExtractRequirements,
  useRequirements,
  useRequirementVersions,
  useSaveRequirements,
  useWorkspace,
} from "@/lib/api/queries";
import { formatDate } from "@/lib/format";
import { canEdit, type RequirementSpec } from "@/lib/requirements";
import { briefSchema } from "@/lib/validation";

import { SpecView } from "./spec-view";

type Draft = { spec: RequirementSpec; unparsed: string[]; degraded: boolean };
type Notice = { kind: "ok" | "error"; text: string };

export function RequirementsPanel({ workspaceId }: { workspaceId: string }) {
  const workspace = useWorkspace(workspaceId);
  const requirements = useRequirements(workspaceId);
  const extract = useExtractRequirements(workspaceId);
  const save = useSaveRequirements(workspaceId);
  // `null` until the textarea is touched, so it starts from the saved brief.
  const [typed, setTyped] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [briefError, setBriefError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);

  if (requirements.isPending) return <p className="text-muted">Loading the requirements…</p>;
  if (requirements.isError) {
    return (
      <div role="alert" className="border-l-4 border-unmet bg-surface px-4 py-3">
        <p className="font-medium">The requirements could not be loaded.</p>
        <button type="button" className="link mt-1" onClick={() => requirements.refetch()}>
          Try again
        </button>
      </div>
    );
  }

  const current = requirements.data;
  const editable = workspace.data ? canEdit(workspace.data.my_role) : false;
  const brief = typed ?? current?.current.raw_text ?? "";

  async function onRead(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = briefSchema.safeParse({ text: brief });
    if (!parsed.success) {
      setBriefError(parsed.error.issues[0]?.message ?? "Check the text.");
      return;
    }
    setBriefError(null);
    setNotice(null);
    try {
      const result = await extract.mutateAsync(parsed.data.text);
      setDraft({ spec: result.spec, unparsed: result.unparsed, degraded: result.degraded });
    } catch (cause) {
      setBriefError(cause instanceof ApiError ? cause.message : "The brief could not be read.");
    }
  }

  async function onSave() {
    if (!draft) return;
    const expectedVersion = current?.current_version ?? 0;
    setNotice(null);
    try {
      const saved = await save.mutateAsync({
        spec: draft.spec,
        text: brief.trim() === "" ? null : brief.trim(),
        expectedVersion,
        changeNote: null,
      });
      setDraft(null);
      setTyped(null);
      setNotice({
        kind: "ok",
        text:
          saved.current_version === expectedVersion
            ? "Nothing changed, so no new version was saved."
            : `Saved as version ${saved.current_version}.`,
      });
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === "requirement_version_conflict") {
        await requirements.refetch();
        setNotice({
          kind: "error",
          text: "Someone saved a newer version while you were editing. It is shown below; check it, then save yours again.",
        });
        return;
      }
      setNotice({
        kind: "error",
        text: cause instanceof ApiError ? cause.message : "The requirements could not be saved.",
      });
    }
  }

  return (
    <div className="space-y-10">
      {notice ? (
        <p
          role={notice.kind === "ok" ? "status" : "alert"}
          className={`border-l-4 bg-surface px-4 py-3 font-medium ${
            notice.kind === "ok" ? "border-met" : "border-unmet"
          }`}
        >
          {notice.text}
        </p>
      ) : null}

      {editable ? (
        <section aria-labelledby="brief-title">
          <h2 id="brief-title" className="mb-1 text-xl">
            Describe what you need
          </h2>
          <form onSubmit={onRead} noValidate className="max-w-3xl">
            <label htmlFor="brief" className="mb-2 block text-muted">
              Write it the way you would tell a friend: budget, must-haves, what you will use it
              for.
            </label>
            <textarea
              id="brief"
              className="field min-h-32"
              maxLength={4000}
              value={brief}
              onChange={(event) => setTyped(event.target.value)}
              aria-invalid={briefError ? true : undefined}
              aria-describedby={briefError ? "brief-error" : undefined}
              placeholder="A laptop under $1,500 for programming and travel. Must have at least 16 GB RAM, ideally under 1.4 kg."
            />
            {briefError ? (
              <p id="brief-error" role="alert" className="mt-2 text-sm font-medium text-unmet">
                {briefError}
              </p>
            ) : null}
            <button type="submit" className="button mt-3" disabled={extract.isPending}>
              {extract.isPending ? "Reading…" : "Read my brief"}
            </button>
          </form>
        </section>
      ) : null}

      {draft ? (
        <section aria-labelledby="draft-title" className="border-2 border-ink bg-surface p-5">
          <h2 id="draft-title" className="mb-1 text-xl">
            Draft, not saved yet
          </h2>
          <p className="mb-4 max-w-xl text-muted">
            Check what was understood. Set what is a must-have and how much the rest matters.
          </p>
          {draft.degraded ? (
            <p className="mb-4 max-w-xl text-sm">
              The AI reader was unavailable, so the built-in reader was used. It understands less.
            </p>
          ) : null}
          <SpecView spec={draft.spec} onChange={(spec) => setDraft({ ...draft, spec })} />
          {draft.unparsed.length > 0 ? (
            <div className="mt-5 max-w-xl">
              <h3 className="font-semibold">Not understood</h3>
              <p className="text-sm text-muted">
                These parts were left out. Reword them in the brief and read it again.
              </p>
              <ul className="mt-1 list-disc pl-5">
                {draft.unparsed.map((clause) => (
                  <li key={clause}>{clause}</li>
                ))}
              </ul>
            </div>
          ) : null}
          <div className="mt-5 flex flex-wrap gap-3">
            <button type="button" className="button" onClick={onSave} disabled={save.isPending}>
              {save.isPending ? "Saving…" : "Save requirements"}
            </button>
            <button type="button" className="button-quiet" onClick={() => setDraft(null)}>
              Discard draft
            </button>
          </div>
        </section>
      ) : null}

      <section aria-labelledby="current-title">
        <h2 id="current-title" className="mb-1 text-xl">
          Current requirements
        </h2>
        {current ? (
          <>
            <p className="mb-4 text-muted">
              Version {current.current_version}, saved {formatDate(current.current.created_at)}.
            </p>
            <SpecView spec={current.current.spec} />
            {editable && !draft ? (
              <button
                type="button"
                className="button-quiet mt-5"
                onClick={() =>
                  setDraft({ spec: current.current.spec, unparsed: [], degraded: false })
                }
              >
                Change priorities
              </button>
            ) : null}
          </>
        ) : (
          <p className="max-w-xl text-muted">
            {editable
              ? "None yet. Describe what you need above; products are scored against it."
              : "None yet. An owner or editor of this workspace can add them."}
          </p>
        )}
      </section>

      {current ? <VersionHistory workspaceId={workspaceId} /> : null}
    </div>
  );
}

function VersionHistory({ workspaceId }: { workspaceId: string }) {
  const versions = useRequirementVersions(workspaceId);
  if (!versions.data || versions.data.length < 2) return null;
  return (
    <section aria-labelledby="history-title">
      <h2 id="history-title" className="mb-3 text-xl">
        Version history
      </h2>
      <ol className="max-w-xl divide-y divide-line border-y border-line">
        {versions.data.map((version) => (
          <li key={version.version} className="flex justify-between gap-4 py-2.5">
            <span className="font-semibold">Version {version.version}</span>
            <span className="text-muted">
              {version.change_note ? `${version.change_note} · ` : ""}
              {formatDate(version.created_at)}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
