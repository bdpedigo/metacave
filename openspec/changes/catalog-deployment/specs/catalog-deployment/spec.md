## ADDED Requirements

### Requirement: Helm chart for catalog service
The system SHALL have a Helm chart in `cave-helm-charts/charts/catalog/` providing a Kubernetes Deployment, Service, Ingress, and ConfigMap following existing CAVE chart conventions.

#### Scenario: Deploying the catalog service
- **WHEN** an operator runs `helmfile apply` with catalog values configured
- **THEN** the catalog service SHALL be deployed to the GKE cluster with correct environment variables, resource limits, and ingress routing

#### Scenario: Rolling update with zero downtime
- **WHEN** a new catalog image is deployed via Helm upgrade
- **THEN** the system SHALL perform a rolling update with readiness probes ensuring zero downtime

### Requirement: Cloud SQL provisioning via Terraform
The system SHALL have a Terraform module that provisions a Cloud SQL PostgreSQL instance for the catalog service, with appropriate IAM bindings.

#### Scenario: Provisioning catalog infrastructure
- **WHEN** an operator applies the Terraform module
- **THEN** a Cloud SQL instance SHALL be created with the catalog database, and the catalog service account SHALL have `cloudsql.client` role

### Requirement: IAM for credential vending
The Terraform module SHALL provision a GCP service account for the catalog with `roles/storage.objectViewer` on managed data buckets and `roles/iam.serviceAccountTokenCreator` on itself, bound via Workload Identity.

#### Scenario: Catalog pod can generate downscoped tokens
- **WHEN** the catalog pod runs with Workload Identity configured
- **THEN** the catalog service SHALL be able to generate Credential Access Boundary tokens for managed GCS URIs

### Requirement: AFIS integration
The catalog service URL SHALL be registered in AnnotationFrameworkInfoService's datastack configuration as `catalog_url` so CAVEclient can auto-discover the endpoint.

#### Scenario: CAVEclient discovers catalog URL
- **WHEN** a CAVEclient instance connects to a datastack that has `catalog_url` configured in AFIS
- **THEN** the client SHALL auto-configure `CatalogClient` with that URL

#### Scenario: Graceful degradation when catalog is not configured
- **WHEN** a datastack does not have `catalog_url` in AFIS
- **THEN** CAVEclient SHALL raise a clear error when the user tries to access `client.catalog`

### Requirement: Deployment documentation
The catalog repository SHALL include deployment documentation covering: required environment variables, IAM roles, Helmfile values, migration commands, and rollback procedure.

#### Scenario: New operator deploys catalog
- **WHEN** an operator follows the deployment documentation
- **THEN** they SHALL be able to deploy the catalog to a new CAVE environment without additional guidance
