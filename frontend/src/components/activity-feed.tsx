"use client";

import { useActivity } from "@/lib/api/queries";
import { formatDateTime } from "@/lib/format";

/** What happened in the workspace, newest first. Built by the API from its event stream. */
export function ActivityFeed({ workspaceId }: { workspaceId: string }) {
  const activity = useActivity(workspaceId);
  return (
    <section aria-labelledby="activity-title">
      <h2 id="activity-title" className="mb-3 text-xl">
        Recent activity
      </h2>
      {activity.isPending ? (
        <p className="text-muted">Loading activity…</p>
      ) : activity.isError ? (
        <p role="alert">Activity could not be loaded.</p>
      ) : activity.data.length === 0 ? (
        <p className="max-w-xl text-muted">
          Nothing yet. Comments, votes and research show up here.
        </p>
      ) : (
        <ol className="max-w-2xl divide-y divide-line border-y border-line">
          {activity.data.map((item) => (
            <li key={item.id} className="flex justify-between gap-4 py-2">
              <span>{item.summary}</span>
              <span className="shrink-0 text-sm text-muted">
                {formatDateTime(item.occurred_at)}
              </span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
