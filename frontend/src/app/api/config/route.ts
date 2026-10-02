import { NextResponse } from "next/server";

import { publicWsUrl } from "@/lib/server/config";

/**
 * Settings the browser needs at runtime. Read on the server for each request, so
 * the same build runs in every environment (nothing is baked in at build time).
 */
export function GET() {
  return NextResponse.json({ wsUrl: publicWsUrl() }, { headers: { "Cache-Control": "no-store" } });
}
