## ADDED Requirements

### Requirement: Service token configuration
The catalog service SHALL accept a `SERVICE_TOKEN` configuration value (via `SERVICE_TOKEN` environment variable or `.env` file). This value is optional; when absent the service SHALL operate without sending an Authorization header on outbound ME API calls.

#### Scenario: Service token present
- **WHEN** `SERVICE_TOKEN` is set to a non-empty string in the environment
- **THEN** all catalog-to-ME HTTP requests SHALL include `Authorization: Bearer <SERVICE_TOKEN>`

#### Scenario: Service token absent
- **WHEN** `SERVICE_TOKEN` is not set or is empty
- **THEN** catalog-to-ME HTTP requests SHALL be sent without an Authorization header, and ME validation checks SHALL be skipped gracefully

### Requirement: Service identity for ME API calls
The catalog service SHALL authenticate to the MaterializationEngine using its own service token, not the requesting user's token.

#### Scenario: User token is not forwarded
- **WHEN** a user submits a registration or validation request with a valid auth token
- **THEN** the catalog SHALL NOT include the user's token in any outbound ME API call

#### Scenario: ME name-reservation check authenticated
- **WHEN** the catalog performs a name-reservation check against ME `/tables`
- **THEN** the request SHALL carry the catalog's service token (if configured)

#### Scenario: ME mat-table verification authenticated
- **WHEN** the catalog performs a mat-table existence check against ME `/version/{v}/tables`
- **THEN** the request SHALL carry the catalog's service token (if configured)
