## 1. Credential Provider Interface

- [ ] 1.1 Define `CredentialProvider` abstract base class in `cave_catalog/credentials/base.py` with `async def vend(uri: str, ...) -> AccessResponse` method
- [ ] 1.2 Add `AccessResponse` schema to `schemas.py` with fields: `uri`, `format`, `token`, `token_type`, `expires_in`, `storage_provider`, `is_managed`
- [ ] 1.3 Implement URI-scheme dispatch function that maps `gs://` → GCS provider (and returns 422 for unsupported schemes)

## 2. GCS Credential Backend

- [ ] 2.1 Add GCS-related settings to `config.py`: `GCS_PROJECT`, `GCS_SERVICE_ACCOUNT_EMAIL` (optional, for explicit SA identity)
- [ ] 2.2 Implement `GCSCredentialProvider` in `cave_catalog/credentials/gcs.py`: load default credentials, generate Credential Access Boundary token scoped to the asset's URI prefix with read-only access and 1-hour expiry
- [ ] 2.3 Add `google-auth>=2.0` and `google-cloud-iam>=2.0` to project dependencies

## 3. Access Endpoint

- [ ] 3.1 Implement `POST /api/v1/assets/{id}/access` route: look up asset (404 if missing or expired), check auth, dispatch to credential provider for managed assets, return passthrough for unmanaged assets
- [ ] 3.2 Implement permission gating: check datastack read access via middle_auth; if `access_group` is set, check group membership instead
- [ ] 3.3 Wire the new route into the assets router

## 4. Tests

- [ ] 4.1 Write unit tests for `GCSCredentialProvider` with mocked Google auth libraries (token generation, prefix scoping)
- [ ] 4.2 Write integration tests for `/access` endpoint: managed GCS asset returns token, unmanaged asset returns passthrough, expired asset returns 404, missing asset returns 404
- [ ] 4.3 Write auth tests: unauthorized request returns 403, `access_group` membership overrides datastack permission
- [ ] 4.4 Write test for unsupported URI scheme returning 422

## 5. Client Tests

- [ ] 5.1 Write client tests for `get_access()` method against local test server (managed, unmanaged, error cases)
