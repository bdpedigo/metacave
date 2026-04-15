## ADDED Requirements

### Requirement: Asset data model
The system SHALL store assets with the following required fields: `id` (UUID), `datastack` (string), `name` (string), `mat_version` (nullable integer — the CAVE materialization version, if applicable), `revision` (integer, default 1 — the asset's own iteration), `uri` (string), `format` (string), `asset_type` (string), `owner` (string), `is_managed` (boolean), `mutability` (enum: `"static"` or `"mutable"`), `maturity` (enum: `"stable"`, `"draft"`, or `"deprecated"`), `properties` (JSON object), and `created_at` (timestamp). The system SHALL also store optional fields: `expires_at` (timestamp) for TTL lifecycle and `access_group` (nullable string) for future per-asset permissions. Uniqueness SHALL be enforced via two partial unique indexes: `(datastack, name, mat_version, revision)` where `mat_version IS NOT NULL`, and `(datastack, name, revision)` where `mat_version IS NULL`.

#### Scenario: Unique constraint enforcement
- **WHEN** a registration request provides a `(datastack, name, mat_version, revision)` tuple that already exists
- **THEN** the system SHALL reject the request with a 409 Conflict response including the existing asset's ID

#### Scenario: Unique constraint with NULL mat_version
- **WHEN** two registration requests provide the same `(datastack, name, revision)` with `mat_version: null`
- **THEN** the system SHALL reject the second request with a 409 Conflict response

#### Scenario: Same name at different mat versions
- **WHEN** assets are registered with the same `(datastack, name, revision)` but different `mat_version` values
- **THEN** the system SHALL accept both registrations as distinct assets

#### Scenario: Optional expiry
- **WHEN** an asset is registered without an `expires_at` value
- **THEN** the system SHALL store the asset with a NULL `expires_at`, meaning it is retained indefinitely

### Requirement: Asset registration with synchronous validation
The system SHALL accept asset registration via `POST /api/v1/assets/register` with a JSON body containing the required asset fields. Registration SHALL perform synchronous validation in the following order: (1) caller authorization, (2) duplicate check, (3) URI reachability via HEAD request, (4) format sniff by checking for format-specific metadata (e.g., `_delta_log/` for Delta, `info` file for precomputed). When `properties.source` is `"materialization"`, the system SHALL additionally verify the claimed mat table and version exist by querying the MaterializationEngine API. Validation SHALL complete within a reasonable synchronous timeout.

#### Scenario: Successful registration of a Delta table
- **WHEN** an authorized user POSTs a valid registration with `format: "delta"` and a reachable URI containing a `_delta_log/` directory
- **THEN** the system SHALL create the asset and return 201 Created with the asset record including its generated `id`

#### Scenario: Unreachable URI
- **WHEN** a registration request provides a URI that returns a non-success response to a HEAD request
- **THEN** the system SHALL return 422 with a validation error detail indicating `uri_reachable` check failed

#### Scenario: Format sniff mismatch
- **WHEN** a registration request claims `format: "delta"` but the URI does not contain a `_delta_log/` directory
- **THEN** the system SHALL return 422 with a validation error detail indicating `format_sniff` check failed

#### Scenario: Materialization source verification
- **WHEN** a registration request has `properties.source: "materialization"` and `properties.source_table: "synapses_v2"` and `properties.mat_version: 943`
- **AND** the MaterializationEngine confirms that `synapses_v2` exists at version 943
- **THEN** the system SHALL create the asset and return 201 Created

#### Scenario: Materialization source verification failure
- **WHEN** a registration request has `properties.source: "materialization"` but the claimed mat table or version does not exist in MaterializationEngine
- **THEN** the system SHALL return 422 with a validation error detail indicating `mat_table_verify` check failed

#### Scenario: Unauthorized registration
- **WHEN** a caller without write permission on the specified datastack attempts to register an asset
- **THEN** the system SHALL return 403 Forbidden

### Requirement: Materialization table name reservation
The system SHALL check asset names at registration time against the set of materialization table names for the same datastack (queried from the MaterializationEngine `/tables` endpoint across all versions). If the name matches a materialization table, the system SHALL reject the registration unless the caller has admin/service-level write permission and sets `properties.source` to `"materialization"`. Layout variants (names matching `{mat_table}.{suffix}`) SHALL also be reserved. The dry-run validation endpoint SHALL also perform this check.

#### Scenario: Regular user blocked from registering a mat table name
- **WHEN** a regular user attempts to register an asset named `synapses` in a datastack where `synapses` exists as a materialization table
- **THEN** the system SHALL return 409 with a message indicating the name is reserved for materialization

#### Scenario: Mat service registers a mat table name
- **WHEN** a caller with admin/service write permission registers an asset named `synapses` with `properties.source: "materialization"`
- **THEN** the system SHALL accept the registration

#### Scenario: Layout variant of a reserved name is also reserved
- **WHEN** a regular user attempts to register an asset named `synapses.by_pre_root` and `synapses` exists as a materialization table
- **THEN** the system SHALL return 409 with a message indicating the name is reserved (layout variants of mat table names are reserved)

### Requirement: Dry-run validation endpoint
The system SHALL provide `POST /api/v1/assets/validate` which accepts the same request body as `/api/v1/assets/register` and runs the identical validation pipeline (caller authorization, duplicate check, URI reachability, format sniff, source-conditional checks) but SHALL NOT create an asset record. The response SHALL return a structured validation report listing each check with its pass/fail status and any error details.

#### Scenario: All validations pass
- **WHEN** an authorized user POSTs a valid asset body to `/api/v1/assets/validate` and all checks pass
- **THEN** the system SHALL return 200 with a validation report where every check has `passed: true`

#### Scenario: Some validations fail
- **WHEN** a user POSTs an asset body to `/api/v1/assets/validate` where the URI is unreachable but other checks pass
- **THEN** the system SHALL return 200 with a validation report showing `uri_reachable: { passed: false, message: "..." }` and all other checks with their actual results

#### Scenario: Duplicate detected in dry run
- **WHEN** a user POSTs an asset body to `/api/v1/assets/validate` and an asset with the same `(datastack, name, mat_version, revision)` already exists
- **THEN** the system SHALL return 200 with a validation report showing `duplicate_check: { passed: false, existing_id: "..." }`
The system SHALL provide `GET /api/v1/assets/` for listing assets, scoped by datastack. The endpoint SHALL support filtering by query parameters: `datastack` (required), `name` (optional), `mat_version` (optional), `revision` (optional), `format` (optional), `asset_type` (optional), `mutability` (optional), `maturity` (optional). The system SHALL NOT return assets whose `expires_at` is in the past.

#### Scenario: List all assets for a datastack
- **WHEN** an authorized user GETs `/api/v1/assets/?datastack=minnie65_public`
- **THEN** the system SHALL return all non-expired assets for that datastack

#### Scenario: Filter by name and mat_version
- **WHEN** an authorized user GETs `/api/v1/assets/?datastack=minnie65_public&name=synapses&mat_version=943`
- **THEN** the system SHALL return all revisions of synapses at mat version 943

#### Scenario: Filter by maturity
- **WHEN** an authorized user GETs `/api/v1/assets/?datastack=minnie65_public&maturity=stable`
- **THEN** the system SHALL return only stable assets

#### Scenario: Expired assets excluded
- **WHEN** an asset has `expires_at` in the past
- **THEN** the system SHALL NOT include it in listing results

### Requirement: Asset retrieval by ID
The system SHALL provide `GET /api/v1/assets/{id}` to retrieve a single asset by its UUID. The system SHALL return 404 if the asset does not exist or has expired.

#### Scenario: Retrieve existing asset
- **WHEN** an authorized user GETs `/api/v1/assets/{id}` for a valid, non-expired asset
- **THEN** the system SHALL return the full asset record

#### Scenario: Retrieve expired asset
- **WHEN** an authorized user GETs `/api/v1/assets/{id}` for an asset whose `expires_at` is in the past
- **THEN** the system SHALL return 404

### Requirement: Asset deletion
The system SHALL provide `DELETE /api/v1/assets/{id}` to remove an asset record from the catalog. This SHALL only remove the catalog entry, NOT the underlying data in the bucket. The caller MUST have write permission on the asset's datastack.

#### Scenario: Successful deletion
- **WHEN** an authorized user DELETEs `/api/v1/assets/{id}` for an existing asset
- **THEN** the system SHALL remove the asset record and return 204 No Content

#### Scenario: Unauthorized deletion
- **WHEN** a caller without write permission attempts to delete an asset
- **THEN** the system SHALL return 403 Forbidden

### Requirement: Datastack-scoped read permissions
The system SHALL check that the requesting user has read access to the asset's datastack via middle_auth before returning asset metadata. A nullable `access_group` field SHALL be reserved for future per-asset permissions: when NULL, datastack permissions apply; when set, group membership in middle_auth SHALL be checked instead.

#### Scenario: Authorized read via datastack permission
- **WHEN** a user with read access to datastack `minnie65_public` requests an asset in that datastack
- **THEN** the system SHALL return the asset

#### Scenario: Unauthorized read
- **WHEN** a user without read access to the asset's datastack requests it
- **THEN** the system SHALL return 403 Forbidden
