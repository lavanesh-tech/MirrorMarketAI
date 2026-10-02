import { apiFetch } from "./client.js";

const enc = encodeURIComponent;

export const api = {
  register: (email, password) =>
    apiFetch("/auth/register", { method: "POST", body: { email, password } }),
  login: (email, password) =>
    apiFetch("/auth/login", { method: "POST", body: { email, password } }),

  listProjects: (token) => apiFetch("/projects?limit=100", { token }),
  createProject: (token, body) => apiFetch("/projects", { method: "POST", body, token }),
  getProject: (token, id) => apiFetch(`/projects/${enc(id)}`, { token }),
  listClaims: (token, projectId) =>
    apiFetch(`/projects/${enc(projectId)}/claims?limit=100`, { token }),

  getClaim: (token, id) => apiFetch(`/claims/${enc(id)}`, { token }),
  getTimeline: (token, id) => apiFetch(`/claims/${enc(id)}/timeline`, { token }),
  listEvaluations: (token, id) => apiFetch(`/claims/${enc(id)}/evaluations`, { token }),
  listEvidencePackages: (token, id) => apiFetch(`/claims/${enc(id)}/evidence-packages`, { token }),
};
