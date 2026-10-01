# Events: outbox, Kafka, idempotent consumers, dead letters (Phase 21)

```
API request ──► PostgreSQL transaction: business rows + outbox_events row
                        │ (commit)
event-worker: relay ──► Kafka topic mirrormarket.events.v1 (key = workspace or product id)
event-worker: consumer ──► one transaction: processed_events row + workspace_activity row
                        └─ after N failed attempts ──► mirrormarket.events.v1.dlq
```

## Guarantees

| Step | Guarantee | How |
| --- | --- | --- |
| Change → event | Atomic: an event exists if and only if the change committed | Outbox row written in the same transaction |
| Outbox → Kafka | At-least-once, in insert order per key | Relay marks a row published only after the broker acknowledged it (`acks=all`, idempotent producer); it stops a batch at the first failure |
| Kafka → handler | At-least-once | Offsets are committed manually, after the handler's transaction committed |
| Handler effects | Exactly-once effect | `processed_events (consumer, event_id)` is inserted in the same transaction as the effects; a redelivery finds the row and is skipped |
| Poison messages | Never block a partition, never dropped | Retries with backoff, then a copy goes to the `.dlq` topic with the error in headers; if that send fails, the offset is not committed |

## Envelope

```json
{"id": "<uuid>", "type": "comment.created", "version": 1, "occurred_at": "<ISO 8601>",
 "workspace_id": "<uuid|null>", "actor_id": "<uuid|null>", "product_id": "<uuid|null>",
 "data": {}}
```

Event types: `comment.created`, `comment.deleted`, `vote.changed`, `agent_run.completed`,
`prices.recorded`. Consumers ignore unknown fields and unknown types.

## Running it

- `make up` starts Kafka (KRaft, single node) and the `event-worker` container.
- `make smoke-events` posts a comment and waits for it in `GET /workspaces/{id}/activity`.
- `python -m app.workers.event_worker --role relay|consumer|all`; several copies are safe
  (`FOR UPDATE SKIP LOCKED` for the relay, a consumer group for consumers).
- Host tools reach the broker at `localhost:9094`.

## Operations notes

- Outbox rows are always written, even with `KAFKA_ENABLED=false`; they are published once a
  worker runs. Published rows are deleted after `OUTBOX_RETENTION_HOURS`.
- A row that fails `OUTBOX_MAX_ATTEMPTS` times is marked `failed_at` and kept for inspection.
- The activity feed is eventually consistent (normally about a second behind).
- Realtime WebSocket events (Phase 20) still use Redis pub/sub: they are transient hints,
  while Kafka carries the durable event stream.

## Known limits

- No dead-letter replay tool yet; no schema registry (JSON with a `version` field).
- One consumer transaction per event (simple and safe, not the fastest).
