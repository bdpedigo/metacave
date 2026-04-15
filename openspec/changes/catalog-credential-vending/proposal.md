## Why

The catalog service can register and discover assets, but consumers have no way to actually read managed data. Cloud storage buckets are private, and handing out long-lived keys is insecure. The service needs to vend short-lived, prefix-scoped credentials gated by the user's middle_auth permissions so that analytical tools (DuckDB, Polars) can read data directly from GCS (and later S3) without broad bucket access.

## What Changes

- Define a `CredentialProvider` interface (common abstraction for GCS and S3 backends).
- Implement the GCS backend: use a dedicated GCP service account + Credential Access Boundaries to generate prefix-scoped, read-only OAuth tokens with 1-hour expiry.
- Add `POST /api/v1/assets/{id}/access` endpoint — looks up the asset, verifies middle_auth read permission (datastack-level or `access_group`), routes to the correct credential backend based on URI scheme, and returns a token bundle. Unmanaged assets pass through the URI with no token.
- Gate credentials on datastack read access (or `access_group` membership when set) via middle_auth.
- Add `get_access()` client method to CAVEclient (already wired, needs server-side implementation).
- S3 STS backend deferred to a future change.

## Capabilities

### New Capabilities

- `credential-vending`: Cloud-agnostic credential vending for managed bucket prefixes (GCS initially), middle_auth permission gating, passthrough for unmanaged/public assets.

### Modified Capabilities

<!-- None — this is additive to the existing asset-registry capability. -->

## Impact

- `submodules/catalog/src/cave_catalog/` — new `credentials/` package with provider interface, GCS backend, and router
- `submodules/catalog/src/cave_catalog/routers/assets.py` — new `/assets/{id}/access` route
- `submodules/catalog/src/cave_catalog/config.py` — new settings for GCP service account path / project
- `submodules/catalog/src/cave_catalog/schemas.py` — new `AccessResponse` schema
- `submodules/catalog/tests/` — new test module for credential vending
- Infrastructure: GCP service account with `roles/iam.serviceAccountTokenCreator` and bucket-level read access
- No breaking changes to existing endpoints or client methods
