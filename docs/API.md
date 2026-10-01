# API guide

Base URL (local): `http://127.0.0.1:8000/api/v1`. Interactive docs: `/api/v1/docs`.

| Artefact | Where | How it is kept true |
| --- | --- | --- |
| OpenAPI 3.1 contract | `docs/api/openapi.json`, live at `/api/v1/openapi.json` | generated from the code |
| Endpoint index | `docs/api/ENDPOINTS.md` | generated from the contract |
| Postman collection | `docs/api/mirrormarket.postman_collection.json` (+ environment) | generated from the contract |
| Realtime protocol | `docs/REALTIME.md` | WebSockets are outside OpenAPI |

Regenerate with `make api-docs`. A test fails when the committed files differ from what the
code produces, so the documentation cannot silently go stale. Another test runs the Postman
collection's own example requests, in order, against the real API.

## Quickstart (curl)

```bash
API=http://127.0.0.1:8000/api/v1

curl -s $API/auth/register -H 'Content-Type: application/json' \
  -d '{"email":"demo@example.com","password":"correct-horse-battery-staple","display_name":"Demo"}'

TOKEN=$(curl -s $API/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"demo@example.com","password":"correct-horse-battery-staple"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

WS=$(curl -s $API/workspaces -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"Laptop for college"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')

curl -s -X PUT $API/workspaces/$WS/requirements -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"expected_version":0,"text":"A laptop under $1500 with 16 GB RAM and a 14 inch screen."}'
```

In Postman: import the collection and the environment, run **auth → Create an account**, then
**auth → Exchange email + password for an access token**. Tokens and the ids of what you
create are stored in collection variables and reused by later requests.

## Conventions

**Authentication.** `Authorization: Bearer <access token>` (15 minutes). `POST /auth/refresh`
exchanges the single-use refresh token for a new pair. Public routes: health, ready, register,
login, refresh, logout.

**Errors.** One shape for every error, including validation errors:

```json
{"error": {"code": "workspace_not_found", "message": "Workspace not found.", "request_id": "..."}}
```

| Status | Meaning |
| --- | --- |
| 400 | Malformed request (for example an invalid `Idempotency-Key`) |
| 401 | Missing, invalid, expired or revoked token |
| 403 | You are a member, but your role is too low |
| 404 | Not found, or you are not a member of that workspace |
| 409 | Conflict (duplicate, stale `expected_version`, request already in progress) |
| 413 | Body or file too large |
| 422 | Validation failed or a business rule rejected the request (`details` lists the fields) |
| 429 | Rate limited; wait `Retry-After` seconds |
| 502 / 503 | An upstream fetch failed / a dependency is unavailable |

**Pagination.** `?limit=` (1-100) and `?offset=`; responses include the total.

**Idempotency.** `Idempotency-Key: <any unique string>` on POST/PATCH. A retry returns the
stored response with `Idempotent-Replayed: true`.

**Concurrency.** Requirements use optimistic locking: send `expected_version`; a stale value
returns 409.

**Correlation.** Every response has `X-Request-ID`; send your own to trace a call across logs.

**Caching.** Responses are `Cache-Control: no-store`; price history reports `X-Cache: HIT|MISS`.

## Versioning

The path carries the major version (`/api/v1`). Within v1, changes are additive: new
endpoints, new optional request fields, new response fields. Clients must ignore unknown
response fields. Operation ids (`create_workspace`, `ask`, ...) are stable and safe to use for
generated clients.
