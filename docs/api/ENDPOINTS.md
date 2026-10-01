# API endpoints

Generated from `openapi.json` (67 operations); do not edit by hand. Regenerate with `make api-docs`.

Auth: **token** = `Authorization: Bearer <access token>`; **public** = none.

## health

Liveness and readiness probes (no authentication).

| Method | Path | What it does | Auth |
| --- | --- | --- | --- |
| GET | `/api/v1/health` | Liveness probe | public |
| GET | `/api/v1/ready` | Readiness probe | public |

## auth

Accounts, sessions (access + refresh tokens), own audit trail.

| Method | Path | What it does | Auth |
| --- | --- | --- | --- |
| POST | `/api/v1/auth/register` | Create an account | public |
| POST | `/api/v1/auth/login` | Exchange email + password for an access token | public |
| POST | `/api/v1/auth/refresh` | Exchange a refresh token for a new access + refresh token (rotation) | public |
| POST | `/api/v1/auth/logout` | End this session (revokes its refresh tokens) | public |
| POST | `/api/v1/auth/logout-all` | End every session on every device (access tokens stop working too) | token |
| POST | `/api/v1/auth/change-password` | Change the password; all other sessions are ended and a new one is returned | token |
| GET | `/api/v1/auth/audit-logs` | Your own security events, newest first | token |
| GET | `/api/v1/auth/me` | The authenticated user | token |

## workspaces

Comparison workspaces, members, workspace audit trail.

| Method | Path | What it does | Auth |
| --- | --- | --- | --- |
| GET | `/api/v1/workspaces` | Workspaces you are a member of | token |
| POST | `/api/v1/workspaces` | Create Workspace | token |
| GET | `/api/v1/workspaces/{workspace_id}` | Get Workspace | token |
| PATCH | `/api/v1/workspaces/{workspace_id}` | Update workspace details (OWNER or EDITOR) | token |
| GET | `/api/v1/workspaces/{workspace_id}/members` | List Members | token |
| GET | `/api/v1/workspaces/{workspace_id}/audit-logs` | Security-relevant events in this workspace, newest first (OWNER only) | token |

## workspace products

Products being compared in a workspace.

| Method | Path | What it does | Auth |
| --- | --- | --- | --- |
| GET | `/api/v1/workspaces/{workspace_id}/products` | List Products | token |
| POST | `/api/v1/workspaces/{workspace_id}/products` | Add a catalog product to the workspace (OWNER or EDITOR) | token |
| DELETE | `/api/v1/workspaces/{workspace_id}/products/{product_id}` | Remove a product from the workspace (OWNER or EDITOR) | token |

## products

Global product catalog: variants, identifiers, specs.

| Method | Path | What it does | Auth |
| --- | --- | --- | --- |
| GET | `/api/v1/products` | Search the catalog | token |
| POST | `/api/v1/products` | Create Product | token |
| GET | `/api/v1/products/by-identifier` | Find a product by GTIN/UPC/EAN, MPN, ASIN or SKU | token |
| GET | `/api/v1/products/{product_id}` | Get Product | token |
| POST | `/api/v1/products/{product_id}/variants` | Add Variant | token |
| POST | `/api/v1/products/{product_id}/identifiers` | Add Identifier | token |
| PUT | `/api/v1/products/{product_id}/specifications` | Insert or replace specification values (all-or-nothing) | token |

## prices

Price observations and bucketed price history.

| Method | Path | What it does | Auth |
| --- | --- | --- | --- |
| GET | `/api/v1/products/{product_id}/prices` | Price history (bucketed per retailer) and statistics | token |
| POST | `/api/v1/products/{product_id}/prices` | Record price observations (idempotent per retailer/currency/timestamp) | token |

## sources

Evidence sources: safe URL ingestion and uploads.

| Method | Path | What it does | Auth |
| --- | --- | --- | --- |
| GET | `/api/v1/products/{product_id}/sources` | Shared sources plus sources from your workspaces | token |
| POST | `/api/v1/products/{product_id}/sources` | Register a source (URL or placeholder) for a product | token |
| POST | `/api/v1/products/{product_id}/sources/upload` | Upload a PDF, HTML, Markdown or text document as a source | token |
| POST | `/api/v1/sources/{source_id}/ingest` | Fetch the source URL safely, snapshot it and extract text | token |
| GET | `/api/v1/sources/{source_id}` | Get Source | token |
| GET | `/api/v1/sources/{source_id}/document` | Get Document | token |

## embeddings

Chunking and embedding jobs for ingested documents.

| Method | Path | What it does | Auth |
| --- | --- | --- | --- |
| POST | `/api/v1/sources/{source_id}/embed` | Chunk and embed the source's latest document now (idempotent) | token |
| GET | `/api/v1/sources/{source_id}/chunks` | List Chunks | token |
| GET | `/api/v1/embedding-jobs/{job_id}` | Get Embedding Job | token |

## search

Hybrid (lexical + vector) search over workspace evidence.

| Method | Path | What it does | Auth |
| --- | --- | --- | --- |
| POST | `/api/v1/workspaces/{workspace_id}/search` | Hybrid (full-text + vector, RRF) search over the workspace's sources | token |

## requirements

Purchase requirements with immutable versions.

| Method | Path | What it does | Auth |
| --- | --- | --- | --- |
| POST | `/api/v1/workspaces/{workspace_id}/requirements/extract` | Preview the structured spec for a free-text brief (nothing is saved) | token |
| GET | `/api/v1/workspaces/{workspace_id}/requirements` | Current requirements | token |
| PUT | `/api/v1/workspaces/{workspace_id}/requirements` | Save a new requirements version (optimistic locking via expected_version) | token |
| GET | `/api/v1/workspaces/{workspace_id}/requirements/versions` | List Requirement Versions | token |
| GET | `/api/v1/workspaces/{workspace_id}/requirements/versions/{version}` | Get Requirement Version | token |
| GET | `/api/v1/workspaces/{workspace_id}/requirements/diff` | What changed between two versions | token |

## evidence

Evidence packs and citation validation.

| Method | Path | What it does | Auth |
| --- | --- | --- | --- |
| GET | `/api/v1/workspaces/{workspace_id}/evidence-packs` | List Evidence Packs | token |
| POST | `/api/v1/workspaces/{workspace_id}/evidence-packs` | Search and freeze the results as citable evidence (E1..En) | token |
| GET | `/api/v1/workspaces/{workspace_id}/evidence-packs/{pack_id}` | Get Evidence Pack | token |
| POST | `/api/v1/workspaces/{workspace_id}/evidence-packs/{pack_id}/validate` | Check that text cites this pack correctly ([E1] markers, numbers, quotes) | token |

## agents

Agent runs: research, reviews, compatibility, value, risk, synthesis, orchestration, comparison and Ask.

| Method | Path | What it does | Auth |
| --- | --- | --- | --- |
| POST | `/api/v1/workspaces/{workspace_id}/products/{product_id}/research` | Run the Product Research Agent for a workspace product (cited facts) | token |
| POST | `/api/v1/workspaces/{workspace_id}/products/{product_id}/reviews/analyze` | Run the Review Intelligence Agent (aspect sentiment over REVIEW sources) | token |
| POST | `/api/v1/workspaces/{workspace_id}/products/{product_id}/compatibility` | Run the Compatibility Agent against owned devices (request or requirements) | token |
| POST | `/api/v1/workspaces/{workspace_id}/products/{product_id}/value` | Run the Value Agent: price, budget fit, requirement fit, value index | token |
| POST | `/api/v1/workspaces/{workspace_id}/products/{product_id}/risk` | Run the Risk Agent: warranty, returns, safety, reliability + other agents' flags | token |
| POST | `/api/v1/workspaces/{workspace_id}/products/{product_id}/synthesize` | Synthesis Agent: one verdict from the latest runs (no agents are re-run) | token |
| POST | `/api/v1/workspaces/{workspace_id}/analyze` | Run all agents for the workspace's products (bounded) and rank them | token |
| POST | `/api/v1/workspaces/{workspace_id}/compare` | Comparison matrix: weighted scores, hard constraints, sensitivity | token |
| POST | `/api/v1/workspaces/{workspace_id}/ask` | Ask MirrorMarket: grounded answer with enforced [E#] citations (or abstain) | token |
| GET | `/api/v1/workspaces/{workspace_id}/agent-runs` | List Agent Runs | token |
| GET | `/api/v1/workspaces/{workspace_id}/agent-runs/{run_id}` | Get Agent Run | token |

## collaboration

Comments, votes, presence and the activity feed.

| Method | Path | What it does | Auth |
| --- | --- | --- | --- |
| GET | `/api/v1/workspaces/{workspace_id}/comments` | Comments, oldest first (deleted ones are kept as placeholders) | token |
| POST | `/api/v1/workspaces/{workspace_id}/comments` | Comment on the workspace or on one of its products (MEMBER+) | token |
| PATCH | `/api/v1/workspaces/{workspace_id}/comments/{comment_id}` | Edit your own comment | token |
| DELETE | `/api/v1/workspaces/{workspace_id}/comments/{comment_id}` | Delete a comment (author or workspace OWNER); the text is erased | token |
| PUT | `/api/v1/workspaces/{workspace_id}/products/{product_id}/vote` | Vote a workspace product up (1) or down (-1), or remove your vote (0) (MEMBER+) | token |
| GET | `/api/v1/workspaces/{workspace_id}/votes` | Vote totals per product + my vote | token |
| GET | `/api/v1/workspaces/{workspace_id}/presence` | Members currently connected | token |
| GET | `/api/v1/workspaces/{workspace_id}/activity` | Activity feed, newest first (built from events by a Kafka consumer) | token |
