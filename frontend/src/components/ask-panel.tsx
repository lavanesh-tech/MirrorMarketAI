"use client";

import { useState, type FormEvent } from "react";

import { parseAnswer, splitCitations } from "@/lib/agents";
import { ApiError } from "@/lib/api/client";
import { useAsk, useEvidencePack, useLatestRun, useWorkspace } from "@/lib/api/queries";
import { formatDateTime } from "@/lib/format";
import { canRun } from "@/lib/requirements";
import { questionSchema } from "@/lib/validation";

/** Ask a question; the answer may only say what the workspace's sources support. */
export function AskPanel({ workspaceId }: { workspaceId: string }) {
  const workspace = useWorkspace(workspaceId);
  const latest = useLatestRun(workspaceId, "ask");
  const ask = useAsk(workspaceId);
  const [error, setError] = useState<string | null>(null);

  const run = latest.data ?? null;
  const answer = run ? parseAnswer(run.output) : null;
  const pack = useEvidencePack(
    workspaceId,
    answer && !answer.abstained ? run?.evidence_pack_id : null,
  );
  const allowed = workspace.data ? canRun(workspace.data.my_role) : false;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = questionSchema.safeParse(Object.fromEntries(new FormData(event.currentTarget)));
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message ?? "Check the question.");
      return;
    }
    setError(null);
    try {
      await ask.mutateAsync(parsed.data.question);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "The question could not be answered.");
    }
  }

  const cited = pack.data?.items.filter((item) => answer?.cited.includes(item.marker)) ?? [];

  return (
    <div className="space-y-8">
      {allowed ? (
        <section aria-labelledby="ask-title">
          <h2 id="ask-title" className="mb-1 text-xl">
            Ask about these products
          </h2>
          <form onSubmit={onSubmit} noValidate className="max-w-3xl">
            <label htmlFor="question" className="mb-2 block text-muted">
              Answers come only from this workspace&apos;s sources, with a citation for every claim.
              If the sources do not say, you are told so.
            </label>
            <div className="flex flex-wrap gap-3">
              <input
                id="question"
                name="question"
                className="field min-w-0 flex-1"
                maxLength={500}
                placeholder="How long does the battery last?"
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? "question-error" : undefined}
              />
              <button type="submit" className="button shrink-0" disabled={ask.isPending}>
                {ask.isPending ? "Reading the sources…" : "Ask"}
              </button>
            </div>
            {error ? (
              <p id="question-error" role="alert" className="mt-2 text-sm font-medium text-unmet">
                {error}
              </p>
            ) : null}
          </form>
        </section>
      ) : (
        <p className="text-muted">Viewers can read the last answer but not ask questions.</p>
      )}

      {latest.isPending ? (
        <p className="text-muted">Loading the last answer…</p>
      ) : answer && run ? (
        <section aria-labelledby="answer-title" aria-live="polite" className="max-w-3xl">
          <p className="text-sm text-muted">Asked {formatDateTime(run.created_at)}</p>
          <h2 id="answer-title" className="mt-1 text-xl">
            {answer.question}
          </h2>
          {answer.abstained ? (
            <p className="mt-3 border-l-4 border-ink bg-surface px-4 py-3">
              {answer.message ?? "The sources do not answer this."} Add a source that covers it on
              the Products page, then ask again.
            </p>
          ) : (
            <>
              <p className="mt-3 text-lg leading-relaxed">
                {splitCitations(answer.answer).map((part, index) =>
                  "marker" in part ? (
                    <a
                      key={index}
                      href={`#evidence-${part.marker}`}
                      className="link mx-0.5 text-sm font-semibold"
                      aria-label={`Source ${part.marker}`}
                    >
                      [{part.marker}]
                    </a>
                  ) : (
                    // Only real claims are highlighted, not the punctuation between citations.
                    <span key={index} className={/[\p{L}\p{N}]/u.test(part.text) ? "mark" : ""}>
                      {part.text}
                    </span>
                  ),
                )}
              </p>
              {answer.dropped_sentences.length > 0 ? (
                <p className="mt-2 text-sm text-muted">
                  {answer.dropped_sentences.length === 1
                    ? "One sentence was removed"
                    : `${answer.dropped_sentences.length} sentences were removed`}{" "}
                  because no source supported it.
                </p>
              ) : null}
              <h3 className="mt-6 mb-2 font-semibold">Sources cited</h3>
              {pack.isPending ? (
                <p className="text-muted">Loading the sources…</p>
              ) : pack.isError ? (
                <p role="alert">The cited sources could not be loaded.</p>
              ) : (
                <ol className="divide-y divide-line border-y border-line">
                  {cited.map((item) => (
                    <li key={item.marker} id={`evidence-${item.marker}`} className="py-3">
                      <p className="text-sm text-muted">
                        <span className="font-semibold text-ink">[{item.marker}]</span>{" "}
                        {item.source_url ? (
                          <a
                            href={item.source_url}
                            className="link"
                            rel="noreferrer noopener"
                            target="_blank"
                          >
                            {item.source_title}
                          </a>
                        ) : (
                          item.source_title
                        )}
                      </p>
                      <blockquote className="mt-1 border-l-4 border-mark pl-3 whitespace-pre-wrap">
                        {item.text}
                      </blockquote>
                    </li>
                  ))}
                </ol>
              )}
            </>
          )}
          <p className="mt-3 text-sm text-muted">
            Answered by {run.engine.startsWith("rules") ? "the built-in reader" : run.engine}
            {run.degraded ? " (the AI model was unavailable)" : ""}.
          </p>
        </section>
      ) : null}
    </div>
  );
}
