"use client";

import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { collaborationKeys, fetchRealtimeTicket } from "./api/queries";

export type RealtimeStatus = "connecting" | "live" | "off";
type Realtime = { status: RealtimeStatus; online: string[] };

const RealtimeContext = createContext<Realtime>({ status: "off", online: [] });

/** Connection state and who is here, for anything inside a `RealtimeProvider`. */
export function useRealtime(): Realtime {
  return useContext(RealtimeContext);
}

const MAX_RETRY_MS = 30_000;
// The API closes with these when retrying cannot help: not a member, or too many tabs.
const FINAL_CLOSE_CODES = new Set([4404, 4429]);

type ServerMessage = {
  type?: string;
  presence?: string[];
  heartbeat_seconds?: number;
  data?: { user_id?: string };
};

/** What each server event makes stale. The socket only says "something changed". */
export function refreshFor(queryClient: QueryClient, workspaceId: string, event: string): void {
  const invalidate = (queryKey: readonly unknown[]) =>
    void queryClient.invalidateQueries({ queryKey });
  if (event.startsWith("comment.")) {
    invalidate(collaborationKeys.comments(workspaceId));
  } else if (event === "vote.changed") {
    invalidate(collaborationKeys.votes(workspaceId));
  } else if (event === "agent_run.completed") {
    invalidate(["workspaces", workspaceId, "runs"]);
    invalidate(["workspaces", workspaceId, "comparison"]);
  } else {
    return;
  }
  invalidate(collaborationKeys.activity(workspaceId));
}

/**
 * Keeps one WebSocket open for a workspace. It is push-only: the server tells us
 * that something changed and we refetch through the normal API, so permissions and
 * data shapes live in one place. If the socket cannot be opened the app still works;
 * it just stops updating by itself.
 */
export function RealtimeProvider({
  workspaceId,
  children,
}: {
  workspaceId: string;
  children: ReactNode;
}) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<Realtime>({ status: "connecting", online: [] });

  useEffect(() => {
    let socket: WebSocket | null = null;
    let heartbeat: ReturnType<typeof setInterval> | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let attempts = 0;
    let stopped = false;

    function scheduleRetry() {
      if (stopped) return;
      setState((current) => ({ ...current, status: "off" }));
      attempts += 1;
      retry = setTimeout(connect, Math.min(MAX_RETRY_MS, 1000 * 2 ** (attempts - 1)));
    }

    async function connect() {
      if (stopped) return;
      setState((current) => ({ ...current, status: "connecting" }));
      let url: string;
      let ticket: string;
      try {
        const config = (await (await fetch("/api/config")).json()) as { wsUrl: string };
        ticket = await fetchRealtimeTicket(workspaceId);
        url = `${config.wsUrl}/api/v1/ws/workspaces/${workspaceId}`;
      } catch {
        scheduleRetry();
        return;
      }
      if (stopped) return;
      const current = new WebSocket(url);
      socket = current;
      current.onopen = () => current.send(JSON.stringify({ type: "auth", ticket }));
      current.onmessage = (event) => {
        let message: ServerMessage;
        try {
          message = JSON.parse(String(event.data)) as ServerMessage;
        } catch {
          return;
        }
        const userId = message.data?.user_id;
        if (message.type === "ready") {
          attempts = 0;
          setState({ status: "live", online: message.presence ?? [] });
          clearInterval(heartbeat);
          heartbeat = setInterval(
            () => current.send(JSON.stringify({ type: "ping" })),
            (message.heartbeat_seconds ?? 20) * 1000,
          );
        } else if (message.type === "presence.joined" && userId) {
          setState((now) => ({ ...now, online: [...new Set([...now.online, userId])] }));
        } else if (message.type === "presence.left" && userId) {
          setState((now) => ({ ...now, online: now.online.filter((id) => id !== userId) }));
        } else if (message.type) {
          refreshFor(queryClient, workspaceId, message.type);
        }
      };
      current.onclose = (event) => {
        clearInterval(heartbeat);
        if (socket !== current) return;
        if (FINAL_CLOSE_CODES.has(event.code)) {
          setState({ status: "off", online: [] });
          return;
        }
        // Includes the normal end of a connection when its ticket's lifetime is up.
        scheduleRetry();
      };
    }

    void connect();
    return () => {
      stopped = true;
      clearInterval(heartbeat);
      clearTimeout(retry);
      const closing = socket;
      socket = null;
      closing?.close();
    };
  }, [workspaceId, queryClient]);

  return <RealtimeContext.Provider value={state}>{children}</RealtimeContext.Provider>;
}
