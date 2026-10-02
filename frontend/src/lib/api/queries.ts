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

// ------------------------------------------------------------------ helpers
type Result<T> = { data?: T; error?: unknown; response: Response };

function unwrap<T>(result: Result<T>): T {
  if (result.data === undefined) throw toApiError(result.error, result.response.status);
  return result.data;
}

const inWorkspace = (id: string) => ({ params: { path: { workspace_id: id } } });

// ------------------------------------------------------------- requirements
export type Requirements = Schemas["RequirementsResponse"];
export type RequirementSpecInput = Schemas["RequirementSpec-Input"];
export type Extraction = Schemas["ExtractResponse"];
export type RequirementVersion = Schemas["RequirementVersionSummary"];

export const requirementKeys = {
  current: (id: string) => ["workspaces", id, "requirements"] as const,
  versions: (id: string) => ["workspaces", id, "requirements", "versions"] as const,
};

/** The current requirements, or `null` when the workspace has none yet. */
export function useRequirements(id: string) {
  return useQuery({
    queryKey: requirementKeys.current(id),
    queryFn: async (): Promise<Requirements | null> => {
      const result = await api.GET(
        "/api/v1/workspaces/{workspace_id}/requirements",
        inWorkspace(id),
      );
      if (result.response.status === 404 && errorCode(result.error) === "requirement_not_found") {
        return null;
      }
      return unwrap(result);
    },
  });
}

export function useRequirementVersions(id: string) {
  return useQuery({
    queryKey: requirementKeys.versions(id),
    queryFn: async (): Promise<RequirementVersion[]> => {
      const result = await api.GET("/api/v1/workspaces/{workspace_id}/requirements/versions", {
        params: { path: { workspace_id: id }, query: { limit: 20 } },
      });
      return unwrap(result).items;
    },
  });
}

/** Turn a free-text brief into a structured draft. Nothing is saved. */
export function useExtractRequirements(id: string) {
  return useMutation({
    mutationFn: async (text: string): Promise<Extraction> =>
      unwrap(
        await api.POST("/api/v1/workspaces/{workspace_id}/requirements/extract", {
          ...inWorkspace(id),
          body: { text },
        }),
      ),
  });
}

export function useSaveRequirements(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    // `expectedVersion` is the version this edit started from. If someone else saved
    // in the meantime the API answers 409 instead of overwriting their work.
    mutationFn: async (input: {
      spec: RequirementSpecInput;
      text: string | null;
      expectedVersion: number;
      changeNote: string | null;
    }): Promise<Requirements> =>
      unwrap(
        await api.PUT("/api/v1/workspaces/{workspace_id}/requirements", {
          ...inWorkspace(id),
          body: {
            spec: input.spec,
            text: input.text,
            expected_version: input.expectedVersion,
            change_note: input.changeNote,
          },
        }),
      ),
    onSuccess: (saved) => {
      queryClient.setQueryData(requirementKeys.current(id), saved);
      return queryClient.invalidateQueries({ queryKey: requirementKeys.versions(id) });
    },
  });
}

// ----------------------------------------------------------------- products
export type WorkspaceProduct = Schemas["WorkspaceProductResponse"];
export type ProductSummary = Schemas["ProductSummary"];
export type ProductDetail = Schemas["ProductDetail"];
export type ProductCreate = Schemas["ProductCreate"];
export type SpecificationInput = Schemas["SpecificationInput"];

export const productKeys = {
  inWorkspace: (id: string) => ["workspaces", id, "products"] as const,
  catalog: (query: string) => ["catalog", query] as const,
  detail: (productId: string) => ["products", productId] as const,
};

export function useWorkspaceProducts(id: string) {
  return useQuery({
    queryKey: productKeys.inWorkspace(id),
    queryFn: async (): Promise<WorkspaceProduct[]> =>
      unwrap(await api.GET("/api/v1/workspaces/{workspace_id}/products", inWorkspace(id))),
  });
}

/** Catalog search. Disabled until there is something to search for. */
export function useCatalogSearch(query: string) {
  return useQuery({
    queryKey: productKeys.catalog(query),
    enabled: query.length > 0,
    queryFn: async (): Promise<ProductSummary[]> => {
      const result = await api.GET("/api/v1/products", {
        params: { query: { q: query, limit: 10 } },
      });
      return unwrap(result).items;
    },
  });
}

export function useProduct(productId: string, enabled = true) {
  return useQuery({
    queryKey: productKeys.detail(productId),
    enabled,
    queryFn: async (): Promise<ProductDetail> =>
      unwrap(
        await api.GET("/api/v1/products/{product_id}", {
          params: { path: { product_id: productId } },
        }),
      ),
  });
}

export function useAddWorkspaceProduct(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (productId: string): Promise<WorkspaceProduct> =>
      unwrap(
        await api.POST("/api/v1/workspaces/{workspace_id}/products", {
          ...inWorkspace(id),
          body: { product_id: productId },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: productKeys.inWorkspace(id) }),
  });
}

export function useRemoveWorkspaceProduct(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (productId: string): Promise<void> => {
      const { error, response } = await api.DELETE(
        "/api/v1/workspaces/{workspace_id}/products/{product_id}",
        { params: { path: { workspace_id: id, product_id: productId } } },
      );
      if (!response.ok) throw toApiError(error, response.status);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: productKeys.inWorkspace(id) }),
  });
}

/** Add a product to the shared catalog. */
export function useCreateProduct() {
  return useMutation({
    mutationFn: async (input: ProductCreate): Promise<ProductDetail> =>
      unwrap(await api.POST("/api/v1/products", { body: input })),
  });
}

export function useSaveSpecifications(productId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (specifications: SpecificationInput[]): Promise<ProductDetail> =>
      unwrap(
        await api.PUT("/api/v1/products/{product_id}/specifications", {
          params: { path: { product_id: productId } },
          body: { specifications },
        }),
      ),
    onSuccess: (product) => queryClient.setQueryData(productKeys.detail(productId), product),
  });
}

// --------------------------------------------------------------- comparison
export type AgentRun = Schemas["AgentRunResponse"];

export const comparisonKeys = {
  latest: (id: string) => ["workspaces", id, "comparison"] as const,
};

/** The most recent comparison run, or `null` when none has been made. */
export function useLatestComparison(id: string) {
  return useQuery({
    queryKey: comparisonKeys.latest(id),
    queryFn: async (): Promise<AgentRun | null> => {
      const result = await api.GET("/api/v1/workspaces/{workspace_id}/agent-runs", {
        params: { path: { workspace_id: id }, query: { agent: "comparison", limit: 1 } },
      });
      return unwrap(result).items[0] ?? null;
    },
  });
}

/**
 * Build the comparison. With `research`, every product is researched first (all
 * agents), which is what fills the matrix with values; without it, the stored
 * research is scored again, for example with different weights.
 */
export function useCompare(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      research: boolean;
      weights: Record<string, number>;
      budgetIsHard: boolean;
    }): Promise<AgentRun> => {
      if (input.research) {
        unwrap(await api.POST("/api/v1/workspaces/{workspace_id}/analyze", inWorkspace(id)));
      }
      return unwrap(
        await api.POST("/api/v1/workspaces/{workspace_id}/compare", {
          ...inWorkspace(id),
          body: { weights: input.weights, budget_is_hard: input.budgetIsHard, product_ids: [] },
        }),
      );
    },
    onSuccess: (run) => queryClient.setQueryData(comparisonKeys.latest(id), run),
  });
}

function errorCode(error: unknown): string | undefined {
  return (error as Partial<Schemas["ErrorResponse"]> | undefined)?.error?.code;
}
