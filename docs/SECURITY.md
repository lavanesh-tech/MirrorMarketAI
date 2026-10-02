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
- Errors never contain stack traces or internal details; every response has a request id.

## Known limits

- The PDF active-content check reads names in the raw file; names hidden inside compressed
  object streams are not seen. Parsing never executes PDF content.
- The refresh leeway (`REFRESH_REUSE_LEEWAY_SECONDS`, default 10) means a stolen refresh token
  used within 10 seconds of the real client is not detected as re-use.
- No MFA, no email verification, no password-breach check, no OAuth login yet.
- Rate limits use the socket peer IP; behind a proxy, forwarded headers must be configured
  (Phase 30).
- Secrets come from environment variables; a secrets manager is part of the deployment phases.
