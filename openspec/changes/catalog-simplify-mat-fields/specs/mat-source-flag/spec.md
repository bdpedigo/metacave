## ADDED Requirements

### Requirement: Source field on Asset model
The Asset model SHALL have a `source` column of type String (NOT NULL, default `"user"`) with allowed values `"user"` and `"materialization"`. When `source` is `"materialization"`, the asset's `name` field represents the MaterializationEngine source table name and `mat_version` is the authoritative materialization version.

#### Scenario: Default source for user-created asset
- **WHEN** a caller registers an asset without specifying `source`
- **THEN** the asset SHALL be stored with `source="user"`

#### Scenario: Materialization source set by admin
- **WHEN** an admin caller registers an asset with `source="materialization"`, `name="synapse_table"`, and `mat_version=42`
- **THEN** the asset SHALL be stored with `source="materialization"`, the `name` field as the source table name, and `mat_version=42`

### Requirement: Admin-only gating for materialization source
The system SHALL only permit callers with `is_admin=True` to set `source="materialization"` or provide a non-null `mat_version`. Non-admin callers who include these fields SHALL have them silently dropped (set to `source="user"`, `mat_version=None`).

#### Scenario: Non-admin sends source=materialization
- **WHEN** a non-admin caller sends a registration request with `source="materialization"` and `mat_version=5`
- **THEN** the system SHALL store the asset with `source="user"` and `mat_version=None`, with no error returned

#### Scenario: Non-admin sends mat_version without source
- **WHEN** a non-admin caller sends a registration request with `mat_version=10` but no `source` field
- **THEN** the system SHALL store the asset with `mat_version=None`

#### Scenario: Admin sends full materialization fields
- **WHEN** an admin caller sends `source="materialization"` and `mat_version=3`
- **THEN** the system SHALL store both fields as provided

### Requirement: Warning logged on silent field drop
The system SHALL log a warning-level message when `source` or `mat_version` fields are silently dropped from a non-admin request.

#### Scenario: Dropped fields produce log entry
- **WHEN** a non-admin caller includes `source="materialization"` in a registration request
- **THEN** the system SHALL emit a warning log containing the caller identity and the dropped field names

### Requirement: Properties no longer carries materialization metadata
The system SHALL NOT read `source`, `source_table`, or `mat_version` from the `properties` JSON blob for validation or identity purposes. These keys in `properties` SHALL be treated as opaque user data with no special meaning.

#### Scenario: Properties.source ignored during validation
- **WHEN** a registration request includes `properties={"source": "materialization", "source_table": "foo", "mat_version": 5}` but the top-level `source` is `"user"`
- **THEN** the validation pipeline SHALL NOT trigger materialization table verification

### Requirement: Validation pipeline uses top-level fields
`check_mat_table` SHALL receive the source table name from the asset's `name` field and the version from the top-level `mat_version` field (not from `properties`). Materialization verification SHALL only run when `source="materialization"`.

#### Scenario: Mat table verification uses name and mat_version
- **WHEN** an asset is registered with `source="materialization"`, `name="nucleus_detection"`, `mat_version=10`
- **THEN** `check_mat_table` SHALL verify that table `"nucleus_detection"` exists at version `10` in the MaterializationEngine

#### Scenario: No mat verification for user source
- **WHEN** an asset is registered with `source="user"` (even if `properties` contains source/mat_version keys)
- **THEN** `check_mat_table` SHALL NOT be called
