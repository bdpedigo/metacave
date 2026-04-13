## 1. Project Scaffolding

- [ ] 1.1 Create new service repository/directory with FastAPI project structure (pyproject.toml, src layout, Dockerfile)
- [ ] 1.2 Add SQLAlchemy model for `assets` table (id, datastack, name, mat_version, revision, uri, format, asset_type, owner, is_managed, mutability, maturity, properties JSONB, access_group, created_at, expires_at) with two partial unique indexes for nullable mat_version
- [ ] 1.3 Add Alembic migration for initial `assets` table creation
- [ ] 1.4 Add middle_auth_client dependency and configure auth decorators for read/write permission checks scoped by datastack

## 2. Asset Registry API (Phase 0)

- [ ] 2.1 Implement `POST /api/v1/assets/register` — request validation, dedup check, auth check
- [ ] 2.2 Implement URI reachability validation (HEAD request via cloud-provider-appropriate SDK — GCS or S3 based on URI scheme)
- [ ] 2.3 Implement format sniff validation (check for `_delta_log/`, `info` file, etc. based on declared format)
- [ ] 2.4 Implement source-conditional validation: when `properties.source == "materialization"`, verify mat table and version exist via MaterializationEngine API
- [ ] 2.5 Implement `GET /api/v1/assets/` — list/filter by datastack (required), name, mat_version, revision, format, asset_type, mutability, maturity; exclude expired assets
- [ ] 2.6 Implement `GET /api/v1/assets/{id}` — single asset retrieval, 404 for expired
- [ ] 2.7 Implement `DELETE /api/v1/assets/{id}` — catalog-only deletion with write auth check
- [ ] 2.8 Write tests for all registry endpoints (registration success/failure cases, listing, filtering, deletion, auth)

## 3. Credential Vending (Phase 1)

- [ ] 3.1 Define credential provider interface (common abstraction for GCS and S3 backends)
- [ ] 3.2 Implement GCS credential backend: set up GCP service account, implement Credential Access Boundary token generation (prefix-scoped, read-only, 1-hour expiry)
- [ ] 3.3 Implement `POST /api/v1/assets/{id}/access` — route to appropriate credential backend based on URI scheme, passthrough URI for unmanaged
- [ ] 3.6 (Future) Implement S3 credential backend: STS AssumeRole with inline prefix-scoped policy
- [ ] 3.4 Implement permission gating: check datastack read access (or `access_group` membership if set) before vending
- [ ] 3.5 Write tests for credential vending (managed vs unmanaged, auth failures, expired assets)

## 4. View Definitions (Phase 2)

- [ ] 4.1 Add registration validation for `asset_type: "view"` — require `properties.definition` with `dialect`, `query`, `references` (using `datastack/name/mat_version/revision` path syntax with `latest` keyword support); validate pinned references exist
- [ ] 4.2 Implement `POST /api/v1/assets/{id}/resolve` — resolve `latest` keywords to concrete values, look up referenced assets, vend credentials for each, substitute placeholders with format-appropriate scan expressions, return resolved SQL + credentials + concrete resolved references
- [ ] 4.3 Write tests for view registration, resolution, and error cases (missing refs, unauthorized refs, non-view resolve)

## 5. CAVEclient Integration

- [ ] 5.1 Add `CatalogClient` class to CAVEclient with `list_assets()`, `get_asset()`, `register_asset()`, `delete_asset()` methods (using mat_version/revision parameters)
- [ ] 5.2 Add `get_access()` method for credential vending
- [ ] 5.3 Add `resolve_view()` and `to_duckdb_sql()` methods for view resolution
- [ ] 5.4 Wire `CatalogClient` to `CAVEclient.catalog` property, configured with datastack and auth token
- [ ] 5.5 Write tests for CatalogClient methods

## 6. Deployment

- [ ] 6.1 Create Helm chart for the catalog service (deployment, service, ingress, ConfigMap for env vars)
- [ ] 6.2 Add Terraform module for Cloud SQL instance, cloud IAM bindings (GCS initially), and service account provisioning
- [ ] 6.3 Add catalog service URL to AnnotationFrameworkInfoService datastack configuration
- [ ] 6.4 Write deployment documentation (environment variables, required IAM roles, Helmfile values)
