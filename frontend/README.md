# MirrorMarket web

Next.js (App Router) + React + TypeScript. Run the API first (`make up` in the repo root).

```bash
npm ci
npm run dev        # http://localhost:3000
npm run check      # lint, types, format, API-type drift, unit tests, production build
npm run api:types  # regenerate src/lib/api/schema.d.ts after `make api-docs`
npm run e2e        # Playwright journey; needs the API running and `npm run build` first
```

## How it talks to the API

The browser only ever talks to this app. `src/app/api/v1/[...path]/route.ts` forwards each
call to FastAPI and adds the access token, which lives in an HttpOnly cookie that page
JavaScript cannot read. Login, register and logout go through `src/app/api/session/*`, which
keep the tokens out of the response body. When the access token expires, the forwarder uses
the refresh token once, replaces the cookies and retries.

Types for every path, parameter and response are generated from `docs/api/openapi.json`;
`npm run api:check` fails when they are stale.

## Layout

```
src/app/(auth)        login, register (public)
src/app/(app)         signed-in pages: workspaces, and per workspace
                      overview, requirements, products, compare
src/app/api/session   login / register / logout (cookie handling)
src/app/api/v1        forwarder to the API
src/app/api/config    runtime settings for the browser (WebSocket address)
src/proxy.ts          sends signed-out visitors to /login before a page renders
src/lib/server        server-only code (cookies, token refresh)
src/lib/api           typed client, generated schema, data hooks
src/components        UI
src/lib/realtime.tsx  WebSocket connection: presence and "refetch this" events
tests                 Vitest + Testing Library
e2e                   Playwright: one journey through the real stack
```

## Live updates

The page opens a WebSocket straight to the API (`PUBLIC_WS_URL`). Because the access token
is in an HttpOnly cookie, the page first asks the API (through the forwarder) for a
30-second ticket that is valid only for that workspace's socket, and sends it as the first
message. Events never carry data to render: they tell the page which queries to refetch.
