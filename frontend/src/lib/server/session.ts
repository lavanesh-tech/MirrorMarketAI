/**
 * Session handling for the backend-for-frontend (BFF).
 *
 * The browser never sees a token. After login the Next.js server keeps the access
 * and refresh tokens in HttpOnly cookies and attaches the access token itself when
 * it forwards requests to the API. JavaScript on the page cannot read HttpOnly
 * cookies, so an XSS bug cannot steal the session; SameSite=Lax plus an Origin
 * check stops other sites from sending requests with these cookies (CSRF).
 */
import "server-only";

import type { NextRequest, NextResponse } from "next/server";

import { API_PREFIX, backendUrl } from "./config";

export const ACCESS_COOKIE = "mm_access";
export const REFRESH_COOKIE = "mm_refresh";

export type Tokens = {
  access_token: string;
  expires_at: string;
  refresh_token: string;
  refresh_expires_at: string;
};

function cookieOptions(expires: Date) {
  return {
    httpOnly: true,
    sameSite: "lax" as const,
    secure: process.env.NODE_ENV === "production",
    path: "/",
    expires,
  };
}

export function setSessionCookies(response: NextResponse, tokens: Tokens): void {
  response.cookies.set(
    ACCESS_COOKIE,
    tokens.access_token,
    cookieOptions(new Date(tokens.expires_at)),
  );
  response.cookies.set(
    REFRESH_COOKIE,
    tokens.refresh_token,
    cookieOptions(new Date(tokens.refresh_expires_at)),
  );
}

export function clearSessionCookies(response: NextResponse): void {
  response.cookies.set(ACCESS_COOKIE, "", cookieOptions(new Date(0)));
  response.cookies.set(REFRESH_COOKIE, "", cookieOptions(new Date(0)));
}

export function isTokens(value: unknown): value is Tokens {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return [v.access_token, v.expires_at, v.refresh_token, v.refresh_expires_at].every(
    (field) => typeof field === "string" && field.length > 0,
  );
}

// One refresh per token at a time in this process: parallel requests from one page
// share the result instead of each rotating the token. (The API also tolerates a
// short race, which covers several server instances.)
const inFlight = new Map<string, Promise<Tokens | null>>();

/** Exchange a refresh token for new tokens; null when the session is over. */
export function refreshTokens(refreshToken: string): Promise<Tokens | null> {
  const running = inFlight.get(refreshToken);
  if (running) return running;
  const attempt = (async () => {
    try {
      const response = await fetch(`${backendUrl()}${API_PREFIX}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
        cache: "no-store",
      });
      if (!response.ok) return null;
      const body: unknown = await response.json();
      return isTokens(body) ? body : null;
    } catch {
      return null;
    } finally {
      inFlight.delete(refreshToken);
    }
  })();
  inFlight.set(refreshToken, attempt);
  return attempt;
}

/**
 * Reject state-changing requests that come from another site. Browsers always send
 * `Origin` on cross-site POSTs, so a mismatch means the request was not made by our pages.
 */
export function isSameOrigin(request: NextRequest): boolean {
  if (request.method === "GET" || request.method === "HEAD") return true;
  const origin = request.headers.get("origin");
  if (origin === null) return true; // non-browser clients (curl, tests) send none
  const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host");
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

export function errorBody(code: string, message: string) {
  return { error: { code, message, request_id: null } };
}
