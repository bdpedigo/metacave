## 1. View Registration Validation

- [ ] 1.1 Add `ViewDefinition` schema to `schemas.py` with fields: `dialect` (str), `query` (str), `references` (dict mapping placeholder names to reference path strings)
- [ ] 1.2 Add registration validation for `asset_type: "view"`: require `properties.definition` to be present and conform to `ViewDefinition`; return 422 if missing or malformed
- [ ] 1.3 Implement pinned reference validation: parse reference paths, look up assets with explicit (non-`latest`) `mat_version` and `revision` in the database, return 422 if any do not exist
- [ ] 1.4 Skip validation for references containing `latest` keyword (resolved at query time)

## 2. View Resolution Endpoint

- [ ] 2.1 Add `ResolveResponse` schema to `schemas.py` with fields: `resolved_query`, `credentials` (list of per-asset credential bundles), `dialect`, `resolved_references` (mapping placeholder names → concrete asset ID, path, mat_version, revision)
- [ ] 2.2 Implement `latest` resolution logic: query database for the highest `mat_version` (and then highest `revision`) matching the reference's datastack and name
- [ ] 2.3 Implement `POST /api/v1/assets/{id}/resolve` route: validate asset is a view (400 if not), resolve all references, check auth for each referenced asset, vend credentials for each managed reference, substitute placeholders with format-appropriate scan expressions
- [ ] 2.4 Implement format → scan expression mapping (`delta` → `delta_scan('{uri}')`, `parquet` → `read_parquet('{uri}')`, etc.)

## 3. Tests

- [ ] 3.1 Write tests for view registration: valid view with pinned refs, valid view with `latest` refs, missing definition returns 422, invalid pinned ref returns 422
- [ ] 3.2 Write tests for view resolution: pinned refs resolved correctly, `latest` refs resolved to highest version, nonexistent `latest` ref returns 422, non-view asset returns 400, unauthorized referenced asset returns 403
- [ ] 3.3 Write tests for scan expression substitution with different formats (delta, parquet)

## 4. Client Tests

- [ ] 4.1 Write client tests for `resolve_view()` and `to_duckdb_sql()` methods against local test server
