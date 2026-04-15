## Why

The catalog service currently forwards the end-user's auth token when calling the MaterializationEngine API for validation (name reservation and mat-table verification). This is fragile: it fails whenever the user's token lacks ME read access, the token is empty (e.g., `AUTH_ENABLED=false` local dev), or the ME requires a service-level identity rather than a user identity. The catalog should authenticate as itself — using a dedicated service credential — when making server-to-server calls.

## What Changes

- Add a `SERVICE_TOKEN` config variable (env var / `.env`) that the catalog presents as a `Bearer` token on all outbound ME API calls.
- Remove the forwarding of `user.token` from `run_validation_pipeline`, `check_mat_table`, and `check_name_reservation`.
- When `SERVICE_TOKEN` is not set, ME validation calls are skipped (current graceful-skip behavior is preserved).

## Capabilities

### New Capabilities

- `catalog-service-identity`: The catalog service authenticates to downstream CAVE services (starting with MaterializationEngine) with its own token, independent of the requesting user's identity.

### Modified Capabilities

<!-- No existing spec-level requirement changes -->

## Impact

- `submodules/catalog/src/cave_catalog/config.py` — new `SERVICE_TOKEN` setting
- `submodules/catalog/src/cave_catalog/validation.py` — reads service token from settings; removes `token` parameter thread-through
- `submodules/catalog/src/cave_catalog/routers/assets.py` — stops passing `user.token` to validation pipeline
- `.env` / `.env.example` — document new `SERVICE_TOKEN` variable
- No API contract changes; no breaking changes for clients
