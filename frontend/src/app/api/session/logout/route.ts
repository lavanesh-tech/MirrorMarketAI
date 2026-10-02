import { NextResponse, type NextRequest } from "next/server";

import { API_PREFIX, backendUrl } from "@/lib/server/config";
import { REFRESH_COOKIE, clearSessionCookies, errorBody, isSameOrigin } from "@/lib/server/session";

/** Log out: revoke the session on the API (best effort) and always clear the cookies. */
export async function POST(request: NextRequest): Promise<NextResponse> {
  if (!isSameOrigin(request)) {
    return NextResponse.json(errorBody("cross_site_request", "Request blocked."), { status: 403 });
  }
  const refreshToken = request.cookies.get(REFRESH_COOKIE)?.value;
  if (refreshToken) {
    await fetch(`${backendUrl()}${API_PREFIX}/auth/logout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
      cache: "no-store",
    }).catch(() => undefined);
  }
  const response = NextResponse.json({ ok: true });
  clearSessionCookies(response);
  return response;
}
