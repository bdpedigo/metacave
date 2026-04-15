## Why

Researchers need to run cross-table joins (e.g., synapses × morphology features) locally in DuckDB/Polars, but today they must manually look up URIs, obtain credentials, and write boilerplate scan expressions for each table. The catalog should store SQL view definitions that reference other catalog assets by name, resolve `latest` versions at query time, vend credentials for each referenced asset, and return ready-to-execute SQL — turning a multi-step manual workflow into a single API call.

## What Changes

- Add registration validation for `asset_type: "view"` — require `properties.definition` with `dialect`, `query`, `references` (using `datastack/name/mat_version/revision` path syntax with `latest` keyword support); validate that pinned (non-`latest`) references exist.
- Add `POST /api/v1/assets/{id}/resolve` — resolve `latest` keywords to concrete values, look up referenced assets, vend credentials for each, substitute placeholders with format-appropriate scan expressions, return resolved SQL + credentials + concrete resolved references.
- Add client-side convenience: `resolve_view()` and `to_duckdb_sql()` methods on `CatalogClient` (already wired, needs server-side implementation).

## Capabilities

### New Capabilities

- `view-definitions`: Stored SQL templates referencing catalog assets, resolved at query time by substituting credential-vended URIs for client-side execution in DuckDB/Polars.

### Modified Capabilities

<!-- None — view registration uses the existing asset-registry endpoint with additional validation for the "view" asset_type. -->

## Impact

- `submodules/catalog/src/cave_catalog/routers/assets.py` — new `/assets/{id}/resolve` route, view-specific registration validation
- `submodules/catalog/src/cave_catalog/schemas.py` — new `ResolveResponse`, `ViewDefinition` schemas
- `submodules/catalog/src/cave_catalog/validation.py` — view reference validation logic
- `submodules/catalog/tests/` — new test module for view registration, resolution, and error cases
- Depends on credential-vending being implemented (resolve vends credentials for each referenced asset)
- No breaking changes to existing endpoints or client methods
