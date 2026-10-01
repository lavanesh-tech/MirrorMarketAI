# Realtime collaboration (Phase 20)

One WebSocket per open workspace: `ws(s)://<host>/api/v1/ws/workspaces/{workspace_id}`.
The socket only **pushes** events. Every write (comment, vote, agent run) goes through
the REST API, so validation, RBAC, rate limits and idempotency live in one place.

## Protocol (JSON text frames)

| Direction | Message |
| --- | --- |
| client → server | `{"type":"auth","token":"<access token>"}` within `WS_AUTH_TIMEOUT_SECONDS` |
| server → client | `{"type":"ready","connection_id","user_id","role","presence":[user ids],"heartbeat_seconds"}` |
| client → server | `{"type":"ping"}` every `heartbeat_seconds` (keeps presence fresh) |
| server → client | `{"type":"pong"}` |
| server → client | `{"type":"<event>","workspace_id","actor_id","at","data":{...}}` |

Events: `presence.joined`, `presence.left` (`{user_id, display_name}`), `comment.created`,
`comment.updated` (the comment), `comment.deleted` (`{id, product_id, parent_id}`),
`vote.changed` (`{product_id, up, down, score}`), `agent_run.completed`
(`{run_id, agent, product_id, status}`).

Close codes: `4401` not authenticated or token expired, `4404` not a member,
`4408` auth/idle timeout, `4429` too many connections or messages, `1003` not JSON text,
`1008` origin not allowed, `1009` message too big, `1013` client too slow.

## Design

- **Auth in the first message**, not the URL: browsers cannot set an `Authorization`
  header on a WebSocket, and URLs land in access logs. The socket is closed when the
  access token expires; the client reconnects with a fresh one.
- **No database connection is held** by an open socket: a short session is opened only
  for the membership check.
- **Fan-out across replicas** uses Redis pub/sub (`mm:rt:<workspace>`); each replica
  forwards to its own sockets. Without Redis, or if PUBLISH fails, the event is delivered
  to the local replica's sockets.
- **Events are hints, PostgreSQL is the truth.** Pub/sub is at-most-once with no replay,
  so a client refetches over REST after (re)connecting.
- **Backpressure:** each socket has a bounded send queue and its own writer task; a
  client that cannot keep up is closed with `1013` instead of slowing everyone down.
- **Presence** is a Redis sorted set per workspace (`user:connection` → last heartbeat);
  entries older than `PRESENCE_TTL_SECONDS` are ignored, so a crashed replica's users
  disappear without a clean disconnect. `GET /workspaces/{id}/presence` returns it.
- **Limits:** connections per user per workspace, message size, messages per 10 s, idle
  timeout, and an `Origin` allow-list (the CORS origins).

## Known limits

- Membership is checked at connect time only (there is no member-removal API yet).
- No event replay or sequence numbers; Kafka (Phase 21) carries durable events.
