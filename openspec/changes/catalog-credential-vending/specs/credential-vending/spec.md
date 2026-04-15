## ADDED Requirements

### Requirement: Credential vending for managed assets
The system SHALL provide `POST /api/v1/assets/{id}/access` to vend short-lived cloud storage credentials for managed assets. For assets with `is_managed: true`, the system SHALL generate a provider-appropriate credential scoped to the asset's URI prefix with read-only permissions and a 1-hour expiry. The URI scheme (`gs://` vs `s3://`) SHALL determine which credential backend is used. For GCS assets, the system SHALL use Credential Access Boundaries (downscoped OAuth token). The response SHALL include `uri`, `format`, `token`, `token_type`, `expires_in` (seconds), and `storage_provider` ("gcs" or "s3").

#### Scenario: Successful credential vending for managed GCS asset
- **WHEN** an authorized user POSTs to `/api/v1/assets/{id}/access` for a managed asset with a `gs://` URI
- **THEN** the system SHALL return 200 with a JSON body containing `uri`, `format`, `token` (a valid GCS downscoped OAuth token), `token_type: "Bearer"`, `expires_in: 3600`, and `storage_provider: "gcs"`

#### Scenario: Credential request for unmanaged asset
- **WHEN** an authorized user POSTs to `/api/v1/assets/{id}/access` for an asset with `is_managed: false`
- **THEN** the system SHALL return 200 with a JSON body containing `uri`, `format`, `token: null`, `token_type: null`, `expires_in: null`, and `is_managed: false`

#### Scenario: Unauthorized credential request
- **WHEN** a user without read access to the asset's datastack requests credentials
- **THEN** the system SHALL return 403 Forbidden

#### Scenario: Credential request for expired asset
- **WHEN** a user requests credentials for an asset whose `expires_at` is in the past
- **THEN** the system SHALL return 404

#### Scenario: Credential request for nonexistent asset
- **WHEN** a user requests credentials for an asset ID that does not exist
- **THEN** the system SHALL return 404

### Requirement: Credentials are prefix-scoped
The vended credentials SHALL be scoped to the asset's `uri` prefix using provider-specific mechanisms (GCS Credential Access Boundaries). The credentials SHALL NOT grant access to any objects outside that prefix.

#### Scenario: GCS token cannot access objects outside prefix
- **WHEN** a client uses a vended GCS token to access an object outside the asset's URI prefix
- **THEN** the GCS API SHALL deny the request

### Requirement: Middle_auth permission gating
The system SHALL verify that the requesting user has read access to the asset's datastack via middle_auth before vending credentials. If the asset has a non-NULL `access_group`, the system SHALL check group membership in that group instead of datastack-level permissions.

#### Scenario: Access granted via datastack permission
- **WHEN** a user with read access to the asset's datastack requests credentials
- **THEN** the system SHALL vend the token

#### Scenario: Access granted via asset-level group
- **WHEN** an asset has `access_group: "special-group"` and the requesting user is a member of that group in middle_auth
- **THEN** the system SHALL vend the token regardless of datastack-level permissions

### Requirement: Credential provider abstraction
The system SHALL define a `CredentialProvider` interface that credential backends implement. The interface SHALL have a single `vend(uri, ...) -> AccessResponse` method. New storage providers (e.g., S3) SHALL be addable by implementing this interface and registering for the appropriate URI scheme.

#### Scenario: Unknown URI scheme
- **WHEN** a credential request is made for a managed asset with an unsupported URI scheme (e.g., `az://`)
- **THEN** the system SHALL return 422 indicating the storage provider is not supported
