## Context

The catalog service (Phase 0) is deployed with asset registration, discovery, and validation. Assets have URIs pointing to cloud storage (primarily GCS), but consumers cannot read managed data without pre-existing bucket permissions. The CAVE stack uses middle_auth for identity and permission checks, and services run on GKE with GCP-native IAM.

The parent design (cave-data-catalog) established Decision 4: cloud-agnostic credential vending with provider-specific backends.  GCS uses Credential Access Boundaries (downscoped OAuth), S3 uses STS AssumeRole. This change implements the GCS backend; S3 is deferred.

## Goals / Non-Goals

**Goals:**
- Vend short-lived (1-hour), read-only, prefix-scoped GCS tokens for managed assets
- Gate credential access behind middle_auth datastack read permissions (or `access_group` membership)
- Return passthrough responses for unmanaged assets (URI only, no token)
- Define a `CredentialProvider` interface that S3 can implement later without API changes

**Non-Goals:**
- S3 STS backend implementation (deferred)
- Write credentials — all vended tokens are read-only
- Per-file signed URLs (prefix-scoped tokens are format-agnostic and simpler)
- Credential caching or pooling at the service level (clients cache as needed)

## Decisions

### 1. Credential Access Boundaries for GCS

**Decision**: Use Google's Credential Access Boundary API to downscope the catalog service account's OAuth token to a read-only token scoped to the asset's URI prefix.

**Alternatives considered**:
- Per-file signed URLs — requires enumerating all files (must read Delta log server-side), scales poorly for multi-file formats, and is format-aware.
- V4 signed URLs on the prefix — GCS doesn't support prefix-level signed URLs.
- Granting users direct bucket IAM — not time-bounded, can't scope to prefix.

**Rationale**: A single downscoped token covers all objects under a prefix, is format-agnostic (works for Delta, Parquet, Lance), expires in 1 hour, and is natively supported by `google-cloud-storage` and `fsspec`/`gcsfs` which DuckDB and Polars use under the hood.

### 2. Provider interface with URI-scheme dispatch

**Decision**: A `CredentialProvider` abstract base class with a single `async def vend(uri: str, ...) -> AccessResponse` method. A dispatch function maps URI scheme (`gs://` → GCS, `s3://` → S3) to the appropriate provider.

**Rationale**: Keeps the router thin — it looks up the asset, checks auth, and delegates to the provider. Adding S3 later means implementing one class and registering it for the `s3://` scheme.

### 3. Service account identity

**Decision**: The catalog service runs with a GCP service account that has `roles/storage.objectViewer` on managed buckets and `roles/iam.serviceAccountTokenCreator` on itself (needed for Credential Access Boundary token exchange). The service account key or Workload Identity is configured via standard GCP mechanisms (env var `GOOGLE_APPLICATION_CREDENTIALS` or GKE metadata server).

**Rationale**: Workload Identity is preferred in production (no key file). For local dev, a service account key file works. The `GCS_PROJECT` and `GCS_SERVICE_ACCOUNT_EMAIL` settings are needed for the downscoping API call.

### 4. Permission check flow

**Decision**: The `/access` endpoint checks permissions in this order:
1. If the asset has `access_group` set → check membership in that middle_auth group
2. Otherwise → check datastack-level read permission via middle_auth
3. If the asset is expired (`expires_at` < now) → 404
4. If the asset is not found → 404

This reuses the existing auth infrastructure from Phase 0.

## Risks / Trade-offs

- **[GCP Credential Access Boundary API has a ~1s latency]** → Acceptable for interactive use; clients should cache the returned token for its TTL rather than calling `/access` per query.
- **[Service account needs broad bucket read access]** → Scope bucket IAM to managed-data buckets only. The downscoped token limits what end-users can actually read (prefix-scoped).
- **[Token format may change across GCP SDK versions]** → Return the token as an opaque string; clients pass it to `gcsfs` or `google-cloud-storage` as a bearer token.
