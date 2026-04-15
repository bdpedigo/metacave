## 1. Helm Chart

- [ ] 1.1 Create `cave-helm-charts/charts/catalog/Chart.yaml` with chart metadata following existing chart conventions
- [ ] 1.2 Create Deployment template with container spec, environment variables from ConfigMap, readiness/liveness probes, and resource limits
- [ ] 1.3 Create Service and Ingress templates with TLS and annotations matching existing CAVE ingress patterns
- [ ] 1.4 Create ConfigMap template for all catalog environment variables (`DATABASE_URL`, `AUTH_ENABLED`, `AUTH_SERVICE_URL`, `MAT_ENGINE_URL`, `GCS_PROJECT`, `SERVICE_TOKEN`, `LOG_LEVEL`)
- [ ] 1.5 Create `values.yaml` with sensible defaults and documentation comments

## 2. Terraform Infrastructure

- [ ] 2.1 Add Terraform module for Cloud SQL PostgreSQL instance (db-f1-micro, Postgres 15, private IP in the existing VPC)
- [ ] 2.2 Add GCP service account for the catalog with `roles/cloudsql.client` binding
- [ ] 2.3 Add GCP service account IAM for credential vending: `roles/storage.objectViewer` on managed buckets, `roles/iam.serviceAccountTokenCreator` on itself
- [ ] 2.4 Configure Workload Identity binding between the Kubernetes service account and the GCP service account

## 3. AFIS Integration

- [ ] 3.1 Add `catalog_url` field to the AFIS datastack configuration schema
- [ ] 3.2 Update CAVEclient's info service client to read `catalog_url` and pass it to `CatalogClient` initialization
- [ ] 3.3 Add graceful error handling when `catalog_url` is not configured for a datastack

## 4. Documentation

- [ ] 4.1 Write deployment documentation: prerequisites, environment variables table, IAM roles needed, Helmfile values example
- [ ] 4.2 Document migration procedure: how to run `alembic upgrade head` against production Cloud SQL
- [ ] 4.3 Document rollback procedure: Helm rollback steps, database migration downgrade
