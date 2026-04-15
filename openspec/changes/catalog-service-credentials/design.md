## Context

The catalog service makes server-to-server calls to the MaterializationEngine (ME) during asset registration and validation: once to check name reservation (`/tables`) and once to verify the mat table exists (`/version/{v}/tables`). Both calls were originally unauthenticated; a recent fix forwarded the end-user's Bearer token. Forwarding user tokens for service-to-service calls is fragile — it breaks when the user token is absent (local dev with `AUTH_ENABLED=false`), when the ME requires a service identity, or when the catalog runs in a context where its operator's credentials differ from the user's.

The standard pattern in the CAVE stack is for background service calls to use a dedicated service token configured at deploy time.

## Goals / Non-Goals

**Goals:**
- The catalog reads a `SERVICE_TOKEN` from its own config and sends it as `Authorization: Bearer <token>` on all outbound ME API calls.
- When `SERVICE_TOKEN` is not configured, ME validation calls are skipped gracefully (no regression from current behavior).
- User tokens are never forwarded to downstream services.

**Non-Goals:**
- Automatic token refresh or OAuth client-credentials flow — a static long-lived service token is sufficient for now.
- Extending service-credential use to other downstream services (GCS, S3) — those use ADC / IAM, not Bearer tokens.
- Storing or rotating the token — operational concern outside this change.

## Decisions

### Static bearer token over OAuth client-credentials

**Decision**: Use a single `SERVICE_TOKEN` env var (a pre-generated middle_auth service token).

**Rationale**: All other CAVE services use this pattern — a static token is issued once, stored as a secret, and rotated manually. Adding a full OAuth client-credentials flow would require additional infra (client ID/secret, token endpoint, caching) with no clear benefit at current scale.

**Alternative considered**: Forward the user token only when the service token is absent. Rejected — it conflates two identity concerns and makes behavior inconsistent across deployments.

### Remove token parameter from validation pipeline

**Decision**: `run_validation_pipeline`, `check_mat_table`, and `check_name_reservation` will read the service token directly from `get_settings()` instead of accepting it as a parameter.

**Rationale**: The token is a deployment-level secret, not a per-request value. Reading from settings is simpler and avoids threading a credential through the call stack.

## Risks / Trade-offs

- **Service token with broad ME read access** → Mitigation: use a read-only middle_auth service token; document minimum required scope in `.env.example`.
- **Token not configured in dev** → ME validation checks are skipped (non-blocking), so local dev works without a service token.
- **Token leakage in logs** → Mitigation: never log the token value; the existing `config` debug log already omits it since `SERVICE_TOKEN` is not included in the logged fields.

## Migration Plan

1. Add `SERVICE_TOKEN` to `Settings` (optional, defaults to `None`/empty).
2. Update `check_mat_table` and `check_name_reservation` to read `settings.service_token`.
3. Remove `token` parameter from `run_validation_pipeline` and callers in `assets.py`.
4. Add `SERVICE_TOKEN=` to `.env.example` with a comment.
5. No migration needed — existing deployments without `SERVICE_TOKEN` behave as before (ME checks skipped).

## Open Questions

- Should the catalog eventually use a GCP service account + Workload Identity instead of a static token for production? Deferred to the deployment phase (task 7.x).
