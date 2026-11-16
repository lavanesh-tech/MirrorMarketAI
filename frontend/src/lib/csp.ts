/**
 * Content-Security-Policy for the pages (ADR-052).
 *
 * Scripts run only if they carry this response's nonce, and 'strict-dynamic' lets
 * those scripts load the rest of the app's chunks. Nothing inline without the nonce,
 * no eval outside development, no plugins, no framing, and data is only fetched from
 * this app itself plus the API's WebSocket (live updates connect to the API directly).
 */

export interface CspOptions {
  /** Base64 nonce, new for every response. */
  nonce: string;
  /** Development builds need eval for React's error overlay and fast refresh. */
  development: boolean;
  /** Where the browser opens the WebSocket, for example ws://127.0.0.1:8000. */
  wsUrl: string;
}

/** The scheme, host and port of a WebSocket URL; the path is not part of a CSP source. */
export function wsOrigin(wsUrl: string): string {
  try {
    const url = new URL(wsUrl);
    return url.protocol === "ws:" || url.protocol === "wss:" ? `${url.protocol}//${url.host}` : "";
  } catch {
    return "";
  }
}

export function contentSecurityPolicy({ nonce, development, wsUrl }: CspOptions): string {
  const ws = wsOrigin(wsUrl);
  const directives: Record<string, string[]> = {
    "default-src": ["'self'"],
    "script-src": [
      "'self'",
      `'nonce-${nonce}'`,
      "'strict-dynamic'",
      ...(development ? ["'unsafe-eval'"] : []),
    ],
    "style-src": ["'self'", `'nonce-${nonce}'`],
    "img-src": ["'self'", "data:", "blob:"],
    "font-src": ["'self'"],
    "connect-src": ["'self'", ...(ws ? [ws] : []), ...(development ? ["ws:"] : [])],
    "object-src": ["'none'"],
    "base-uri": ["'self'"],
    "form-action": ["'self'"],
    "frame-ancestors": ["'none'"],
  };
  return Object.entries(directives)
    .map(([name, values]) => `${name} ${values.join(" ")}`)
    .join("; ");
}

/** A fresh nonce: 16 random bytes, base64. Works in Node and in the browser. */
export function newNonce(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return btoa(String.fromCharCode(...bytes));
}
