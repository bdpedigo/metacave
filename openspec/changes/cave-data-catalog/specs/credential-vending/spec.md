## ADDED Requirements

### Requirement: Credential vending for managed assets
The system SHALL provide `POST /api/v1/assets/{id}/access` to vend short-lived cloud storage credentials for managed assets. For assets with `is_managed: true`, the system SHALL generate a provider-appropriate credential scoped to the asset's URI prefix with read-only permissions and a 1-hour expiry. The URI scheme (`gs://` vs `s3://`) SHALL determine which credential backend is used. For GCS assets, the system SHALL use Credential Access Boundaries (downscoped OAuth token). For S3 assets, the system SHALL use STS AssumeRole with an inline policy scoped to the prefix. The response SHALL include `uri`, `format`, `token`, `token_type`, `expires_in` (seconds), and `storage_provider` ("gcs" or "s3").

#### Scenario: Successful credential vending for managed GCS asset
- **WHEN** an authorized user POSTs to `/api/v1/assets/{id}/access` for a managed asset with a `gs://` URI
- **THEN** the system SHALL return 200 with a JSON body containing `uri`, `format`, `token` (a valid GCS downscoped OAuth token), `token_type: "Bearer"`, `expires_in: 3600`, and `storage_provider: "gcs"`

#### Scenario: Successful credential vending for managed S3 asset
- **WHEN** an authorized user POSTs to `/api/v1/assets/{id}/access` for a managed asset with an `s3://` URI
- **THEN** the system SHALL return 200 with a JSON body containing `uri`, `format`, temporary S3 credentials (`access_key_id`, `secret_access_key`, `session_token`), `expires_in: 3600`, and `storage_provider: "s3"`

#### Scenario: Credential request for unmanaged asset
- **WHEN** an authorized user POSTs to `/api/v1/assets/{id}/access` for an asset with `is_managed: false`
- **THEN** the system SHALL return 200 with a JSON body containing `uri`, `format`, `token: null`, `token_type: null`, `expires_in: null`, and `is_managed: false`

#### Scenario: Unauthorized credential request
- **WHEN** a user without read access to the asset's datastack requests credentials
- **THEN** the system SHALL return 403 Forbidden

#### Scenario: Credential request for expired asset
- **WHEN** a user requests credentials for an asset whose `expires_at` is in the past
- **THEN** the system SHALL return 404

### Requirement: Credentials are prefix-scoped
The vended credentials SHALL be scoped to the asset's `uri` prefix using provider-specific mechanisms (GCS Credential Access Boundaries, S3 STS inline policy). The credentials SHALL NOT grant access to any objects outside that prefix.

#### Scenario: GCS token cannot access objects outside prefix
- **WHEN** a client uses a vended GCS token to access an object outside the asset's URI prefix
- **THEN** the GCS API SHALL deny the request

#### Scenario: S3 credentials cannot access objects outside prefix
- **WHEN** a client uses vended S3 credentials to access an object outside the asset's URI prefix
- **THEN** the S3 API SHALL deny the request

### Requirement: Middle_auth permission gating
The system SHALL verify that the requesting user has read access to the asset's datastack via middle_auth before vending credentials. If the asset has a non-NULL `access_group`, the system SHALL check group membership in that group instead of datastack-level permissions.

#### Scenario: Access granted via datastack permission
- **WHEN** a user with read access to the asset's datastack requests credentials
- **THEN** the system SHALL vend the token

#### Scenario: Access granted via asset-level group
- **WHEN** an asset has `access_group: "special-group"` and the requesting user is a member of that group in middle_auth
- **THEN** the system SHALL vend the token regardless of datastack-level permissions
