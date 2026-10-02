import { NextResponse, type NextRequest } from "next/server";

import { API_PREFIX, backendUrl } from "@/lib/server/config";
import { errorBody, isSameOrigin } from "@/lib/server/session";

/** Create an account. The response has no tokens; the page logs in afterwards. */
export async function POST(request: NextRequest): Promise<NextResponse> {
  if (!isSameOrigin(request)) {
    return NextResponse.json(errorBody("cross_site_request", "Request blocked."), { status: 403 });
  }
  try {
    const upstream = await fetch(`${backendUrl()}${API_PREFIX}/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: await request.text(),
      cache: "no-store",
    });
    const body: unknown = await upstream.json().catch(() => null);
    return NextResponse.json(body, { status: upstream.status });
  } catch {
    return NextResponse.json(
      errorBody("api_unavailable", "The service is not reachable. Try again in a moment."),
      { status: 502 },
    );
  }
}
