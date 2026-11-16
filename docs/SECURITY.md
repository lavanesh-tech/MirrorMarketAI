# Security (Phase 22)

What is defended, how, and what is deliberately left out. Each control has tests.

## Authentication and sessions

| Control | How |
| --- | --- |
| Passwords | Argon2id, 12-128 characters, constant-time check even for unknown emails |
| Access token | JWT HS256, 15 minutes, fixed algorithm allow-list, issuer/audience/expiry/type checked |
| Key rotation | `JWT_PREVIOUS_SECRET_KEY` is accepted for verification only; new tokens use the new key |
| Refresh token | Opaque random value, stored only as SHA-256; **rotated on every use** |
| Stolen-token detection | Re-using a rotated refresh token revokes the whole session ("family"), except within a 10-second leeway that absorbs two tabs refreshing at once |
| Session lifetime | Refresh token 14 days; a session can never be refreshed past 30 days |
| Log out everywhere | `POST /auth/logout-all` and password changes bump `users.token_version`; access tokens carry it as `ver`, so older ones are rejected immediately, with no denylist |
| Brute force | Rate limits per IP and per email on login, register, refresh, change-password |

Refresh tokens travel in the JSON body. The web client (Phase 24) decides how to store them.

## Authorization

- Roles OWNER > EDITOR > MEMBER > VIEWER, checked in the service layer.
- A non-member gets **404**, identical to a workspace that does not exist; a member with too
  low a role gets 403.
- `tests/db/test_authz_matrix.py` reads every route from the OpenAPI schema and checks that
  (1) only six documented routes work without a token and (2) no workspace route serves a
  non-member. A new endpoint that forgets a check fails this test automatically.

## Audit trail

`audit_logs` records who did what, to what, from which IP and request, and whether it
worked: logins (including failures), refreshes, token re-use, logouts, password changes,
workspace changes, comment moderation, uploads and rejected uploads.

- **Append-only:** a database trigger rejects UPDATE, DELETE and TRUNCATE.
- **No foreign keys:** the trail survives deletion of the user or workspace.
- **No secrets:** never passwords or tokens; emails of failed logins are stored as a
  truncated hash.
- Written in the same transaction as the action it describes.
- Read with `GET /auth/audit-logs` (your own events) and
  `GET /workspaces/{id}/audit-logs` (OWNER only).

## Prompt injection (evidence is untrusted)

Web pages, reviews and uploads are written by strangers and are put into LLM prompts when
`AGENT_ENGINE=openai`. Layers:

1. The system prompt declares evidence to be data; evidence sits in a delimited block.
2. `neutralize`: hidden characters removed, one line per item, and anything imitating the
   prompt's structure (`[E7]` markers, `SYSTEM:` lines, chat-template tokens, code fences)
   is defanged. An item cannot pose as another item or as a system message.
3. `scan`: pattern heuristics flag instruction-like sentences; those sentences are withheld
   from the LLM (never from the user or the rules engine) and logged.
4. Output is constrained: strict JSON schema, every claim must cite an item that contains it
   (citation validator), and verdicts, prices and rankings are computed in code.

The heuristics are a tripwire, not a guarantee. Measured on synthetic sentences
(`benchmarks/prompt_injection.py`): see PROJECT_STATE for precision and recall, including the
held-out set and its misses. Layers 1, 2 and 4 do not depend on detection.

Phase 27 measured this end to end with poisoned sources (`docs/EVALUATION.md`). The first
run showed the offline engines adopting planted claims and quoting planted instructions,
because the sentence filter only ran before a language model. It now runs for every engine,
and facts are read from documents of record before reviews and notes (ADR-049). Still open:
a plain false statement in a low-trust source can be quoted in an offline answer, and
paraphrased instructions can get past the pattern-based detector.

## Files and URLs

- Type is decided from the bytes, never from the client's Content-Type or file name.
- Executables, archives (including Office files) and scripts are refused; PDFs with active
  content (`/JavaScript`, `/JS`, `/Launch`, `/EmbeddedFile`, `/RichMedia`, `/XFA`), encrypted
  PDFs and PDFs over the page limit are refused.
- Upload size limit, plus a cap on extracted text (decompression bombs).
- File names are reduced to a safe display name; uploads are stored in the database and are
  never executed or served back inline.
- URL ingestion goes through the SSRF-safe fetcher from Phase 5 (private/loopback/link-local
  ranges blocked, DNS pinned, redirects re-validated, port and size limits).

## HTTP

- Security headers on every response: `X-Content-Type-Options`, `X-Frame-Options`,
  `Content-Security-Policy: default-src 'none'` (the Swagger page is exempt),
  `Referrer-Policy`, `Permissions-Policy`, `Cache-Control: no-store`, and HSTS outside
  local/test.
- Request body limit (413), enforced for declared and for streamed bodies.
- Explicit CORS origins (no wildcard); WebSocket origin allow-list.
- Browser WebSockets authenticate with a 30-second ticket bound to one workspace and
  rejected by every REST endpoint (ADR-046); the access token never reaches page scripts.
- Errors never contain stack traces or internal details; every response has a request id.

## Web app (Content-Security-Policy)

- Every page is served with a Content-Security-Policy built per request in
  `frontend/src/proxy.ts` (`src/lib/csp.ts`): a fresh random nonce, `script-src 'self'
  'nonce-…' 'strict-dynamic'` and `style-src 'self' 'nonce-…'` (no `unsafe-inline`),
  `connect-src` limited to the app and the API's WebSocket origin (`PUBLIC_WS_URL`),
  `object-src 'none'`, `base-uri 'self'`, `form-action 'self'`, `frame-ancestors 'none'`.
  `unsafe-eval` is added in development only (React's dev tooling needs it).
- Nonces only work on pages rendered per request, so the root layout opts every page
  out of static rendering (`await connection()`). The pages are small; the cost is a
  render per request.
- The end-to-end journey fails if the browser reports any CSP violation, and CI checks
  the header on the production image.
- The production image (`frontend/Dockerfile`) runs the standalone Next.js server as the
  unprivileged `node` user with a read-only root filesystem; `.env` files and
  agent notes are excluded from the build context.

## Supply chain (CI)

- Images are scanned with Grype before they are published; a critical vulnerability
  with a fix available fails the build. Images go to GitHub Container Registry only
  from `main` or a version tag, after every other job passed, with the job's own
  short-lived `GITHUB_TOKEN` (no stored registry secret).
- Actions outside `actions/*` are pinned to a full commit SHA, because a tag can be
  moved: in 2026 dozens of `aquasecurity/trivy-action` tags were re-pointed to
  malicious code (CVE-2026-33634). For that reason the scanner here is Grype, not the
  Trivy action.
- No Dependabot version-update pull requests: merged bot commits would add a second
  contributor to the repository. Dependabot *alerts* (repository settings) report
  vulnerable dependencies without committing anything; updates are applied by hand.

## Metrics and traces

- `/metrics` exposes counts and timings only: no ids, emails, URLs or model names in
  labels. It has no user authentication; set `METRICS_TOKEN` (bearer token, compared in
  constant time) or keep the API port private. It is not part of the `/api/v1` surface
  the web app proxies.
- Traces never contain request bodies, headers or SQL parameter values; SQL spans hold
  the parameterised statement text only (tested with a login request).
- Prometheus, Grafana and Jaeger listen on 127.0.0.1 only. Grafana allows anonymous
  read-only access for local use; change `GRAFANA_ADMIN_PASSWORD` before exposing it.
- Security signals with alerts: refresh-token re-use, failed-login spikes, sustained
  rate limiting, instruction-like text found in evidence (`docs/OBSERVABILITY.md`).

## Known limits

- The PDF active-content check reads names in the raw file; names hidden inside compressed
  object streams are not seen. Parsing never executes PDF content.
- The refresh leeway (`REFRESH_REUSE_LEEWAY_SECONDS`, default 10) means a stolen refresh token
  used within 10 seconds of the real client is not detected as re-use.
- No MFA, no email verification, no password-breach check, no OAuth login yet.
- Rate limits use the socket peer IP. Behind the load balancer of the scaled setup
  (`docker-compose.scale.yml`) the API trusts `X-Forwarded-For` from any address
  (`FORWARDED_ALLOW_IPS=*`), which is safe only because the API containers there have no
  published port. A deployment must list the balancer's addresses instead.
- The CSP still allows `style-src 'self'` stylesheets and `img-src data: blob:`; there is
  no CSP violation reporting endpoint.
- Secrets come from environment variables; a secrets manager is part of the deployment phases.
