import { NextResponse, type NextRequest } from "next/server";

import { contentSecurityPolicy, newNonce } from "@/lib/csp";

/**
 * Runs before a page renders. It only decides where a visitor may go, based on whether
 * a session cookie exists; the API still verifies every request, so this is a
 * convenience (no flash of a page you cannot use), not the security boundary.
 */
const REFRESH_COOKIE = "mm_refresh";
const AUTH_PAGES = new Set(["/login", "/register"]);

export function proxy(request: NextRequest): NextResponse {
  const { pathname } = request.nextUrl;
  const signedIn = request.cookies.has(REFRESH_COOKIE);

  if (AUTH_PAGES.has(pathname)) {
    return signedIn ? NextResponse.redirect(new URL("/workspaces", request.url)) : render(request);
  }
  if (!signedIn) {
    const login = new URL("/login", request.url);
    if (pathname !== "/") login.searchParams.set("next", pathname);
    return NextResponse.redirect(login);
  }
  return render(request);
}

/**
 * Let the page render with a Content-Security-Policy and its nonce. Next.js reads the
 * policy from the request headers and adds the nonce to the scripts it emits; the
 * browser enforces the copy on the response.
 */
function render(request: NextRequest): NextResponse {
  const nonce = newNonce();
  const policy = contentSecurityPolicy({
    nonce,
    development: process.env.NODE_ENV === "development",
    wsUrl: process.env.PUBLIC_WS_URL ?? "ws://127.0.0.1:8000",
  });
  const headers = new Headers(request.headers);
  headers.set("x-nonce", nonce);
  headers.set("Content-Security-Policy", policy);
  const response = NextResponse.next({ request: { headers } });
  response.headers.set("Content-Security-Policy", policy);
  return response;
}

export const config = {
  // Pages only: not the BFF routes, Next's own assets or files with an extension.
  matcher: ["/((?!api/|_next/|.*\\..*).*)"],
};
