import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { vi } from "vitest";
import { AuthProvider } from "../auth/AuthContext.jsx";

export function jsonResponse(body, status = 200) {
  return new Response(body === null ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// Routes fetch calls by "METHOD path" (path without /api/v1 and query string).
export function mockApi(routes) {
  const calls = [];
  const fetchMock = vi.fn(async (url, options = {}) => {
    const method = options.method || "GET";
    const path = String(url).replace(/^\/api\/v1/, "").split("?")[0];
    calls.push({ method, path, options });
    const handler = routes[`${method} ${path}`];
    if (!handler) return jsonResponse({ title: "Not Found", status: 404 }, 404);
    const result = typeof handler === "function" ? handler(options) : handler;
    return result instanceof Response ? result : jsonResponse(result);
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

export function signIn(token = "test-token") {
  sessionStorage.setItem("proofstack.access_token", token);
  sessionStorage.setItem("proofstack.email", "alice@proofstack.dev");
}

export function renderAt(path, element, routePath = path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <Routes>
          <Route path={routePath} element={element} />
          <Route path="/login" element={<p>login page</p>} />
          <Route path="/projects" element={<p>projects page</p>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}
