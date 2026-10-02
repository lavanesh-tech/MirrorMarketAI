# MirrorMarket web

Next.js (App Router) + React + TypeScript. Run the API first (`make up` in the repo root).

```bash
npm ci
npm run dev        # http://localhost:3000
npm run check      # lint, types, format, API-type drift, unit tests, production build
npm run api:types  # regenerate src/lib/api/schema.d.ts after `make api-docs`
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
src/app/(app)         signed-in pages (workspaces)
src/app/api/session   login / register / logout (cookie handling)
src/app/api/v1        forwarder to the API
src/proxy.ts          sends signed-out visitors to /login before a page renders
src/lib/server        server-only code (cookies, token refresh)
src/lib/api           typed client, generated schema, data hooks
src/components        UI
tests                 Vitest + Testing Library
```
