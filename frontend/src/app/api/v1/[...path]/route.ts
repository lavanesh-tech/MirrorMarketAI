import { NextResponse, type NextRequest } from "next/server";

import { API_PREFIX, backendUrl } from "@/lib/server/config";
import {
  ACCESS_COOKIE,
  REFRESH_COOKIE,
  clearSessionCookies,
  errorBody,
  isSameOrigin,
  refreshTokens,
  setSessionCookies,
  type Tokens,
} from "@/lib/server/session";

/**
 * The API as seen by the browser: same paths as the real API, same origin as the pages.
 *
 * Each request is forwarded to FastAPI with the access token from the HttpOnly cookie.
 * When the access token is missing or rejected, the refresh token is used once to get a
 * new pair, the cookies are replaced, and the request is retried. If that fails the
 * session is over: cookies are cleared and the browser gets 401.
 */

// Endpoints that return tokens in their body must go through /api/session/* instead.
const TOKEN_ENDPOINTS = /^auth\/(login|register|refresh|logout|change-password)$/;
const REQUEST_HEADERS = ["content-type", "accept", "idempotency-key", "x-request-id"];
const RESPONSE_HEADERS = [
  "content-type",
  "x-request-id",
  "retry-after",
  "x-cache",
  "idempotent-replayed",
  "x-ratelimit-limit",
  "x-ratelimit-remaining",
];
const MAX_BODY_BYTES = 6 * 1024 * 1024;

type Context = { params: Promise<{ path: string[] }> };

async function forward(
  request: NextRequest,
  path: string,
  body: ArrayBuffer | undefined,
  accessToken: string | undefined,
): Promise<Response> {
  const headers = new Headers();
  for (const name of REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  return fetch(`${backendUrl()}${API_PREFIX}/${path}${request.nextUrl.search}`, {
    method: request.method,
    headers,
    body,
    cache: "no-store",
    redirect: "manual",
  });
}

async function handle(request: NextRequest, context: Context): Promise<NextResponse> {
  const path = (await context.params).path.map(encodeURIComponent).join("/");
  if (TOKEN_ENDPOINTS.test(path)) {
    return NextResponse.json(errorBody("not_found", "Not found."), { status: 404 });
  }
  if (!isSameOrigin(request)) {
    return NextResponse.json(errorBody("cross_site_request", "Request blocked."), { status: 403 });
  }

  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  const body = hasBody ? await request.arrayBuffer() : undefined;
  if (body && body.byteLength > MAX_BODY_BYTES) {
    return NextResponse.json(errorBody("request_too_large", "The request body is too large."), {
      status: 413,
    });
  }

  let accessToken = request.cookies.get(ACCESS_COOKIE)?.value;
  const refreshToken = request.cookies.get(REFRESH_COOKIE)?.value;
  let renewed: Tokens | null = null;
  let sessionEnded = false;

  const renew = async (): Promise<boolean> => {
    if (!refreshToken || renewed) return false;
    renewed = await refreshTokens(refreshToken);
    if (!renewed) {
      sessionEnded = true;
      return false;
    }
    accessToken = renewed.access_token;
    return true;
  };

  let upstream: Response;
  try {
    if (!accessToken) await renew();
    upstream = await forward(request, path, body, accessToken);
    if (upstream.status === 401 && (await renew())) {
      upstream = await forward(request, path, body, accessToken);
    }
  } catch {
    return NextResponse.json(
      errorBody("api_unavailable", "The service is not reachable. Try again in a moment."),
      { status: 502 },
    );
  }

  const headers = new Headers({ "Cache-Control": "no-store" });
  for (const name of RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  const noContent = upstream.status === 204 || upstream.status === 304;
  const response = new NextResponse(noContent ? null : upstream.body, {
    status: upstream.status,
    headers,
  });
  if (renewed) setSessionCookies(response, renewed);
  if (sessionEnded) clearSessionCookies(response);
  return response;
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
export const DELETE = handle;
