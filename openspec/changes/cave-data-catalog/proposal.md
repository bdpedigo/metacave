## Why

CAVE's materialized annotation data lives exclusively in PostgreSQL, making it inaccessible to modern analytical tools (DuckDB, Polars, Spark) without going through the MaterializationEngine query API. Meanwhile, researchers produce derived datasets (e.g., synapse embeddings, morphology features) that have no home in the CAVE stack at all — they're shared via ad-hoc bucket paths and tribal knowledge. There is no discovery mechanism ("what datasets exist for minnie65?"), no credential management, and no way to formally describe how external feature tables link back to CAVE annotations or segmentation.

## What Changes

- **New service: CAVE Catalog** — a thin FastAPI + PostgreSQL metadata registry for data assets stored in cloud object storage (GCS primarily, with S3 support for public/external datasets and future deployments).
- **Asset registration API** — producers (MaterializationEngine, pipelines, data scientists) write data to buckets independently, then register assets via a REST API with synchronous validation (dedup, auth, URI reachability, format sniffing, source-conditional checks like verifying mat table existence).
- **Discovery API** — consumers list/search/filter assets scoped by datastack and version.
- **Credential vending API** — for managed assets, the service issues prefix-scoped, short-lived credentials gated by middle_auth permissions (GCS downscoped OAuth tokens initially; S3 STS tokens when needed). Unmanaged (public/external) assets pass through URIs without tokens.
- **View definitions** (later phase) — stored SQL templates with asset references, resolved client-side by substituting credential-vended URIs so DuckDB/Polars can execute joins locally.
- **CAVEclient integration** — a new `client.catalog` sub-client for registration, discovery, credential vending, and view resolution.
- **Format-agnostic design** — supports Delta Lake, Lance, Parquet, Iceberg, Neuroglancer precomputed, and arbitrary formats. The catalog stores only discovery/lineage/access metadata; the data format itself (e.g., Delta log) is authoritative for schema and table metadata.

## Capabilities

### New Capabilities
- `asset-registry`: Core CRUD and discovery for data assets — registration with validation, listing/filtering by datastack and version, TTL/expiry lifecycle, extensible properties for CAVE-specific lineage and provenance metadata.
- `credential-vending`: Cloud-agnostic credential vending for managed bucket prefixes (GCS initially, S3 in the future), middle_auth permission gating, passthrough for unmanaged/public assets.
- `view-definitions`: Stored SQL templates referencing catalog assets, resolved at query time by substituting credential-vended URIs for client-side execution in DuckDB/Polars.
- `caveclient-catalog`: CAVEclient sub-client (`client.catalog`) for asset registration, discovery, credential access, and view resolution.

### Modified Capabilities
<!-- No existing capabilities are being modified. This is an entirely new service. -->

## Impact

- **New service deployment**: FastAPI service + own PostgreSQL database, deployed via a new Helm chart in cave-helm-charts. Needs cloud IAM configuration for credential vending (GCP service account for GCS downscoping; AWS IAM role for S3 STS when needed).
- **MaterializationEngine (loose coupling)**: ME (or a sidecar) will call the catalog registration API after producing table dumps. No changes to ME's core code; this is a new integration point.
- **middle_auth**: No changes needed — the catalog uses existing middle_auth_client decorators for auth and existing datastack permission groups for access control.
- **CAVEclient**: New `CatalogClient` sub-module added.
- **Infrastructure**: Terraform module for the catalog's Cloud SQL instance, cloud IAM bindings for credential vending (GCS initially, S3 when needed), and service account provisioning.
- **No breaking changes** to any existing service or API.
