## 1. Project Scaffolding

- [x] 1.1 Create new service repository/directory with FastAPI project structure (pyproject.toml, src layout, Dockerfile). The repo is in submodules/catalog.
- [x] 1.2 Add SQLAlchemy model for `assets` table (id, datastack, name, mat_version, revision, uri, format, asset_type, owner, is_managed, mutability, maturity, properties JSONB, access_group, created_at, expires_at) with two partial unique indexes for nullable mat_version
- [x] 1.3 Add Alembic migration for initial `assets` table creation
- [x] 1.4 Add middle_auth_client dependency and configure auth decorators for read/write permission checks scoped by datastack

## 2. Asset Registry API + Client (Phase 0)

- [x] 2.1 Implement `POST /api/v1/assets/register` — request validation, dedup check, auth check
- [x] 2.1b Extract shared validation pipeline so it can be reused by both register and validate endpoints
- [x] 2.1c Implement `POST /api/v1/assets/validate` — same validation pipeline as register, returns structured pass/fail report without creating an asset
- [x] 2.2 Implement URI reachability validation (HEAD request via cloud-provider-appropriate SDK — GCS or S3 based on URI scheme)
- [x] 2.3 Implement format sniff validation (check for `_delta_log/`, `info` file, etc. based on declared format)
- [x] 2.4 Implement source-conditional validation: when `properties.source == "materialization"`, verify mat table and version exist via MaterializationEngine API
- [x] 2.5 Implement `GET /api/v1/assets/` — list/filter by datastack (required), name, mat_version, revision, format, asset_type, mutability, maturity; exclude expired assets
- [x] 2.6 Implement `GET /api/v1/assets/{id}` — single asset retrieval, 404 for expired
- [x] 2.7 Implement `DELETE /api/v1/assets/{id}` — catalog-only deletion with write auth check
- [x] 2.8 Write tests for all registry endpoints (registration success/failure cases, listing, filtering, deletion, auth)
- [x] 2.9 Add `CatalogClient` class to CAVEclient, wire to `CAVEclient.catalog` property (configured with datastack and auth token)
- [x] 2.10 Add `register_asset()`, `validate_asset()`, `list_assets()`, `get_asset()`, `delete_asset()` methods (using mat_version/revision parameters)
- [x] 2.11 Write client tests for Phase 0 methods against local server

## 3. Local Development & Testing

- [x] 3.1 Flesh out README with a "Local Development" section covering: prerequisites (Docker, uv), cloning and copying `.env.example` to `.env`, starting the stack with `docker compose up --build`, applying migrations with `uv run alembic upgrade head`, and verifying the service is up at `http://localhost:8000/docs`
- [x] 3.2 Add a "Running tests" section to README: explain that tests use SQLite in-memory (no Docker needed), show the `uv run pytest` invocation, and note what the `conftest.py` fixtures provide
- [x] 3.3 Add a "Live-reload development" section to README: running only the postgres container (`docker compose up postgres -d`) and then the service directly with `uv run uvicorn cave_catalog.app:create_app --factory --reload` for fast iteration, including the env var overrides needed (DATABASE_URL pointing at localhost)
- [x] 3.4 Add a "Running migrations" section to README explaining `uv run alembic upgrade head` (against live DB) and how to generate a new migration with `uv run alembic revision --autogenerate -m "<description>"`
- [x] 3.5 Add a "Auth in local dev" section to README: explain the `AUTH_ENABLED=false` flag, what it disables, and how to test against a real middle_auth instance (`AUTH_ENABLED=true` + `AUTH_SERVICE_URL`)
- [x] 3.6 Add a `justfile` (or `Makefile`) with shorthand targets: `just up` (docker compose up --build -d), `just migrate` (alembic upgrade head), `just test` (uv run pytest), `just dev` (postgres only + uvicorn with reload), `just logs` (docker compose logs -f catalog-service)
- [x] 3.7 Verify the full local path end-to-end: start with a fresh clone, follow README steps, run migrations, hit `/api/v1/assets/` via curl or the Swagger UI, run `uv run pytest`, confirm everything passes — fix any gaps found

## 4. Integration Smoke Test with Real Data

- [x] 4.1 Prepare test data: upload a few parquet dumps of minnie65_public materialized tables and a couple of one-off derived tables to a dedicated GCS test bucket (bucket path is configurable via env var)
- [x] 4.2 Set up local environment for real-infra testing: `gcloud auth application-default login` for GCS access, Docker pg from section 3, `AUTH_ENABLED=true` + `AUTH_SERVICE_URL` pointing at the global middle_auth instance (`https://globalv1.daf-apis.com/auth`)
- [x] 4.3 Register real assets via `CatalogClient` using a real middle_auth token and `minnie65_public` as the datastack — verify the full client→server→middle_auth auth handshake works
- [x] 4.4 Validate URI reachability checks work against real GCS objects (using ADC credentials), including format sniff for parquet files
- [x] 4.6 Test error paths: register with a bad GCS URI (non-existent object), wrong datastack, expired/missing auth token — verify appropriate error responses

## 5. Broken Out to Separate Changes

The following sections were broken out into standalone openspec changes:

- **Credential Vending (Phase 1)** → `catalog-credential-vending`
- **View Definitions (Phase 2)** → `catalog-view-definitions`
- **Deployment** → `catalog-deployment`
