## Context

The catalog service runs locally with `docker compose` and `uvicorn` for development. It needs production deployment infrastructure on the existing CAVE GKE cluster. Existing CAVE services (AnnotationEngine, MaterializationEngine, PyChunkedGraph, etc.) follow a common pattern: Helm charts in `cave-helm-charts`, Terraform modules in `terraform-google-cave`, and service URL registration in AnnotationFrameworkInfoService (AFIS).

## Goals / Non-Goals

**Goals:**
- Deploy the catalog service to the existing CAVE GKE cluster following established patterns
- Provision a Cloud SQL PostgreSQL instance for catalog state
- Configure IAM for the credential vending service account (GCS access)
- Register the catalog URL in AFIS so CAVEclient can auto-discover it
- Document all required environment variables, IAM roles, and deployment steps

**Non-Goals:**
- Multi-region or HA deployment (single region, matching existing CAVE infra)
- Custom autoscaling policies (use existing cluster defaults)
- CI/CD pipeline setup (follow existing cloudbuild patterns)
- S3/AWS infrastructure (deferred with S3 credential backend)

## Decisions

### 1. Own Cloud SQL instance

**Decision**: The catalog gets its own Cloud SQL PostgreSQL instance (not shared with MaterializationEngine or AnnotationEngine databases).

**Alternatives considered**:
- Shared Cloud SQL instance with a separate database — saves cost but couples operational concerns (maintenance windows, scaling).
- Per-datastack databases (matching ME/AE pattern) — unnecessary complexity for a single-table schema.

**Rationale**: Operational isolation matches the "own Helm chart, own database" convention established by other CAVE services. The catalog's schema is trivially simple (one table) and doesn't benefit from per-datastack separation.

### 2. Helm chart in cave-helm-charts

**Decision**: Add a `catalog/` chart to `cave-helm-charts/charts/` following the existing chart structure (deployment, service, ingress, ConfigMap). Use the same ingress annotations and TLS configuration as other CAVE services.

**Rationale**: Consistency with existing deployment patterns. Helmfile in CAVEdeployment manages releases.

### 3. AFIS integration for service discovery

**Decision**: Add a `catalog_url` field to the AFIS datastack configuration. CAVEclient reads this to auto-configure `CatalogClient`.

**Alternatives considered**:
- Hardcoded URL in CAVEclient — breaks across deployments.
- DNS-based discovery — not how other CAVE services work.

**Rationale**: Follows the same pattern as `mat_engine_url`, `pycg_url`, etc. in AFIS.

### 4. Workload Identity for GCS credential vending

**Decision**: Use GKE Workload Identity to bind the catalog's Kubernetes service account to a GCP service account with `roles/storage.objectViewer` on managed buckets and `roles/iam.serviceAccountTokenCreator` on itself.

**Rationale**: No service account key files in production. Workload Identity is the GKE standard for pod-level IAM.

## Risks / Trade-offs

- **[Cloud SQL cost]** → A db-f1-micro instance is sufficient initially (~$10/month). Can resize if usage grows.
- **[AFIS schema change]** → Adding a field to AFIS config is additive and non-breaking, but requires coordinating the AFIS deployment with the catalog deployment.
- **[Credential vending IAM setup is manual]** → The Terraform module automates this, but the initial GCS bucket IAM bindings must be applied for each managed bucket.
