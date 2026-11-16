import type { NextConfig } from "next";

// Sent on every page and route. The API's own headers are set by FastAPI; these
// protect the HTML the browser renders. The Content-Security-Policy needs a new nonce
// per response, so it is set in src/proxy.ts instead (src/lib/csp.ts).
const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // The production image runs the self-contained server Next writes to .next/standalone.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  typedRoutes: true,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default config;
