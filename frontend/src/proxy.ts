import { NextResponse, type NextRequest } from "next/server";

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
    return signedIn
      ? NextResponse.redirect(new URL("/workspaces", request.url))
      : NextResponse.next();
  }
  if (!signedIn) {
    const login = new URL("/login", request.url);
    if (pathname !== "/") login.searchParams.set("next", pathname);
    return NextResponse.redirect(login);
  }
  return NextResponse.next();
}

export const config = {
  // Pages only: not the BFF routes, Next's own assets or files with an extension.
  matcher: ["/((?!api/|_next/|.*\\..*).*)"],
};
