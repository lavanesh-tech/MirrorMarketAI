"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, toApiError, type Schemas } from "./client";

export type Workspace = Schemas["WorkspaceResponse"];
export type Member = Schemas["WorkspaceMemberResponse"];
export type User = Schemas["UserResponse"];

export const keys = {
  me: ["me"] as const,
  workspaces: ["workspaces"] as const,
  workspace: (id: string) => ["workspaces", id] as const,
  members: (id: string) => ["workspaces", id, "members"] as const,
};

export function useMe() {
  return useQuery({
    queryKey: keys.me,
    queryFn: async (): Promise<User> => {
      const { data, error, response } = await api.GET("/api/v1/auth/me");
      if (!data) throw toApiError(error, response.status);
      return data;
    },
  });
}

export function useWorkspaces() {
  return useQuery({
    queryKey: keys.workspaces,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/v1/workspaces", {
        params: { query: { limit: 100 } },
      });
      if (!data) throw toApiError(error, response.status);
      return data;
    },
  });
}

export function useWorkspace(id: string) {
  return useQuery({
    queryKey: keys.workspace(id),
    queryFn: async (): Promise<Workspace> => {
      const { data, error, response } = await api.GET("/api/v1/workspaces/{workspace_id}", {
        params: { path: { workspace_id: id } },
      });
      if (!data) throw toApiError(error, response.status);
      return data;
    },
  });
}

export function useMembers(id: string) {
  return useQuery({
    queryKey: keys.members(id),
    queryFn: async (): Promise<Member[]> => {
      const { data, error, response } = await api.GET("/api/v1/workspaces/{workspace_id}/members", {
        params: { path: { workspace_id: id } },
      });
      if (!data) throw toApiError(error, response.status);
      return data;
    },
  });
}

export function useCreateWorkspace() {
  const queryClient = useQueryClient();
  return useMutation({
    // One key per attempt: if the request is retried after a timeout, the API
    // returns the first result instead of creating a second workspace.
    mutationFn: async (input: { name: string; idempotencyKey: string }): Promise<Workspace> => {
      const { data, error, response } = await api.POST("/api/v1/workspaces", {
        body: { name: input.name },
        params: { header: { "Idempotency-Key": input.idempotencyKey } },
      });
      if (!data) throw toApiError(error, response.status);
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.workspaces }),
  });
}
