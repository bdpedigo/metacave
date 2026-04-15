## ADDED Requirements

### Requirement: View asset type
The system SHALL support assets with `asset_type: "view"`. A view asset SHALL have a `properties.definition` object containing: `dialect` (string, e.g., "sql"), `query` (string — SQL template with `{placeholder}` references to other assets), and `references` (object mapping placeholder names to asset reference paths in the format `datastack/name/mat_version/revision`). The special keyword `latest` MAY be used in place of `mat_version` and/or `revision` to indicate the reference should resolve to the highest available value at query time.

#### Scenario: Registering a view with pinned references
- **WHEN** an authorized user registers a view with references like `{"synapses": "minnie65_public/synapses/943/0"}`
- **THEN** the system SHALL validate that the referenced asset exists and return 201 Created

#### Scenario: Registering a view with latest references
- **WHEN** an authorized user registers a view with references like `{"synapses": "minnie65_public/synapses/latest/latest"}`
- **THEN** the system SHALL create the view asset and return 201 Created (validation of `latest` references occurs at resolve time, not registration)

#### Scenario: View with invalid pinned reference
- **WHEN** a view registration references an explicit (non-`latest`) asset path that does not exist in the catalog
- **THEN** the system SHALL return 422 with a validation error indicating the referenced asset was not found

#### Scenario: View registration missing definition
- **WHEN** a user registers an asset with `asset_type: "view"` but `properties.definition` is missing or incomplete
- **THEN** the system SHALL return 422 with a validation error

### Requirement: View resolution endpoint
The system SHALL provide `POST /api/v1/assets/{id}/resolve` for view assets. The endpoint SHALL: (1) resolve any `latest` keywords in references to concrete `mat_version` and `revision` values, (2) look up each referenced asset, (3) vend credentials for each managed referenced asset, (4) substitute placeholder names in the SQL template with format-appropriate scan expressions, and (5) return the resolved SQL string, all vended credentials, and the concrete resolved references.

#### Scenario: Successful view resolution with pinned references
- **WHEN** an authorized user POSTs to `/api/v1/assets/{id}/resolve` for a view with two referenced Delta assets using pinned paths
- **THEN** the system SHALL return 200 with `resolved_query` (SQL with scan expressions substituted), `credentials` (array of per-asset credential objects), `dialect`, and `resolved_references` (mapping of placeholder names to concrete asset UUIDs and paths)

#### Scenario: Successful view resolution with latest references
- **WHEN** an authorized user POSTs to `/api/v1/assets/{id}/resolve` for a view referencing `minnie65_public/synapses/latest/latest`
- **AND** the latest mat_version for synapses is 944 with revision 0
- **THEN** the system SHALL resolve to `minnie65_public/synapses/944/0` and return the resolved query with that asset's URI and credentials

#### Scenario: Resolve latest with no matching assets
- **WHEN** a view references `minnie65_public/nonexistent/latest/latest` and no asset with that name exists
- **THEN** the system SHALL return 422 indicating the reference could not be resolved

#### Scenario: Resolve non-view asset
- **WHEN** a user POSTs to `/api/v1/assets/{id}/resolve` for an asset that is not a view
- **THEN** the system SHALL return 400 indicating the asset is not a view

#### Scenario: Resolve view with unauthorized referenced asset
- **WHEN** a user resolves a view but does not have read access to one of the referenced assets
- **THEN** the system SHALL return 403 indicating insufficient permissions for the referenced asset

### Requirement: View listing
The system SHALL include view assets in standard asset listing results. Views SHALL be filterable via `asset_type=view` query parameter.

#### Scenario: Filter to views only
- **WHEN** an authorized user GETs `/api/v1/assets/?datastack=minnie65_public&asset_type=view`
- **THEN** the system SHALL return only assets with `asset_type: "view"` for that datastack
