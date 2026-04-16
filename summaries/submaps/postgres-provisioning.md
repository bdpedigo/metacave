# PostgreSQL Provisioning for CAVE Services

## Overview

CAVE services that require PostgreSQL (AnnotationEngine and MaterializationEngine) are backed by **Google Cloud SQL for PostgreSQL**. Two deployment systems exist: the legacy `CAVEdeployment` (bash scripts) and the current `terraform-google-cave` (Terraform/Terragrunt). Neither system handles schema initialization — that is left to the application layer.

---

## Current System: terraform-google-cave

### Cloud SQL Instance

File: `modules/local_infrastructure/postgres.tf` (local cluster) and `modules/global_infrastructure/postgres.tf` (global cluster).

- **Version:** PostgreSQL 13
- **Default tier:** `db-custom-4-16384` (4 vCPU, 16 GB RAM)
- **Performance flags configured via Terraform:**
  - `maintenance_work_mem`: 2 GB
  - `temp_file_limit`: 100 GB
  - `work_mem`: 64 MB
  - `max_connections`: 20,000

### Databases Created

Two databases are provisioned on the local cluster's instance:
- `annotation` (for AnnotationEngine)
- `materialization` (for MaterializationEngine)

### Credentials

1. **Pre-requisite (manual):** Before `terraform apply`, a JSON secret must be created in Google Secret Manager:
   ```bash
   printf '{"username":"postgres","password":"<strong-secret>"}' \
     | gcloud secrets create <ENV>-postgres-credentials --data-file=-
   ```
2. **During apply:** Terraform reads the secret via `data.google_secret_manager_secret_version`, decodes the JSON, and creates a `google_sql_user` (`writer`) with those credentials.

### Service Account for Proxy

A dedicated Cloud SQL service account is created with `roles/cloudsql.client` and its key is stored in Google Secret Manager. This is used by the Cloud SQL Auth Proxy sidecar container in each pod.

### Credential Distribution to Pods

File: `modules/local_kubernetes/templates/cloudsql.tpl`

Helmfile templates reference credentials via `ref+gcpsecrets://` and `ref+tfstate://` patterns:
```yaml
cloudsql:
  sqlInstanceName: "ref+tfstate://gs://STATE_BUCKET/.../output.sql_instance_name"
  username: "ref+gcpsecrets://PROJECT_ID/{environment}-postgres-credentials#username"
  password: "ref+gcpsecrets://PROJECT_ID/{environment}-postgres-credentials#password"
  googleSecret: "ref+gcpsecrets://PROJECT_ID/cloudsql-google-secret-{cluster_prefix}-{workspace}"
```
Helmfile resolves these at deploy time and injects them into k8s Secrets/ConfigMaps.

---

## Legacy System: CAVEdeployment

Files: `infrastructure/local/launch_cluster.sh`, `infrastructure/global/launch_cluster.sh`

The legacy system uses `gcloud` CLI commands in bash scripts:

```bash
gcloud sql instances create $SQL_INSTANCE_NAME \
  --database-version=POSTGRES_13 \
  --region=$REGION \
  --cpu=$SQL_INSTANCE_CPU \
  --memory=$SQL_INSTANCE_MEMORY

gcloud sql databases create $SQL_ANNO_DB_NAME --instance=$SQL_INSTANCE_NAME
gcloud sql databases create $SQL_MAT_DB_NAME --instance=$SQL_INSTANCE_NAME

gcloud sql users set-password $POSTGRES_WRITE_USER \
  --instance=$SQL_INSTANCE_NAME \
  --password="$POSTGRES_WRITE_USER_PASSWORD"
```

Credentials are stored as Kubernetes secrets from local JSON key files:
```bash
kubectl create secret generic $CLOUD_SQL_SERVICE_ACCOUNT_SECRET \
  --from-file=${GOOGLE_SECRET_FILENAME}=${KEY_FOLDER}/${CLOUD_SQL_SERVICE_ACCOUNT_NAME}.json
```

Connection strings use `127.0.0.1:3306` because the Cloud SQL Proxy sidecar exposes the remote Cloud SQL instance locally on that port.

---

## Connection Pattern (Both Systems)

All database-connected pods run a **Cloud SQL Auth Proxy sidecar**. This proxy:
- Authenticates to Cloud SQL using the service account credentials
- Tunnels the connection to `127.0.0.1:3306` (legacy) or `127.0.0.1:5432` (new, native PostgreSQL port) inside the pod

Applications connect to `localhost` as if the database were local.

---

## Schema / Database Initialization

**Neither terraform-google-cave nor CAVEdeployment initializes schemas or installs extensions (e.g., PostGIS).**

- Schema creation is handled by the application layer:
  - **DynamicAnnotationDB** (used by both AnnotationEngine and MaterializationEngine) uses SQLAlchemy to create tables dynamically on first use.
  - **MaterializationEngine** also has a `flask migrator auto-migrate` migration job, run as a Kubernetes Job (`materialize_migrations.yml`) during deploy.
- PostGIS must be installed separately (not managed by Terraform).

---

## Which Cluster Gets Which Databases

| Cluster Type | Databases | Services |
|---|---|---|
| **Local** | `annotation`, `materialization` | AnnotationEngine, MaterializationEngine |
| **Global** | `authentication`, `infoservice` | middle_auth, AnnotationFrameworkInfoService |
