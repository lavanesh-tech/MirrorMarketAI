import { NextResponse, type NextRequest } from "next/server";

import { API_PREFIX, backendUrl } from "@/lib/server/config";
import { errorBody, isSameOrigin, isTokens, setSessionCookies } from "@/lib/server/session";

/**
 * Log in: forward the credentials to the API, keep the tokens in HttpOnly cookies,
 * and return only "it worked". The tokens never reach browser JavaScript.
 */
export async function POST(request: NextRequest): Promise<NextResponse> {
  if (!isSameOrigin(request)) {
    return NextResponse.json(errorBody("cross_site_request", "Request blocked."), { status: 403 });
  }
  let upstream: Response;
  try {
    upstream = await fetch(`${backendUrl()}${API_PREFIX}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: await request.text(),
      cache: "no-store",
    });
  } catch {
    return NextResponse.json(
      errorBody("api_unavailable", "The service is not reachable. Try again in a moment."),
      { status: 502 },
    );
  }
  const body: unknown = await upstream.json().catch(() => null);
  if (!upstream.ok || !isTokens(body)) {
    const headers = new Headers();
    const retryAfter = upstream.headers.get("retry-after");
    if (retryAfter) headers.set("Retry-After", retryAfter);
    return NextResponse.json(body ?? errorBody("login_failed", "Could not log in."), {
      status: upstream.ok ? 502 : upstream.status,
      headers,
    });
  }
  const response = NextResponse.json({ ok: true });
  setSessionCookies(response, body);
  return response;
}
