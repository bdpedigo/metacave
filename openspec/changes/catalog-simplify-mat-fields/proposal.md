## Why

The catalog service currently has redundant materialization metadata: `mat_version` exists as a top-level Asset field *and* inside `properties` (as `properties.mat_version`), while `name` on the Asset and `properties.source_table` can diverge even though they should always match for materialization-sourced assets. This duplication creates a consistency hazard — callers must keep both locations in sync, and validation logic has to read from one place while the schema stores in another. Since materialization-sourced assets should always derive their identity (`name`, `mat_version`) from the materialization engine, the interface should enforce that directly rather than relying on callers to pass matching values in two places.

## What Changes

- **Remove `mat_version` as a top-level field** on the Asset model, request schema, and response schema. **BREAKING**
- **Remove `properties.source_table` and `properties.mat_version`** as ad-hoc property conventions; these are replaced by the top-level `name` and a new internal flag.
- **Introduce a `source` field** (enum: `"user"`, `"materialization"`) on the Asset model. Default is `"user"`. When `source` is `"materialization"`, the asset's `name` *is* the source table name and `mat_version` is stored as a single top-level field whose value is authoritative.
- **Restrict `source="materialization"` to service-level callers** — the API should reject or hide this option for normal users. Service identity (e.g., a service account or internal token scope) gates access to this value.
- **Hide materialization-internal fields from the standard user-facing response** — average users querying the catalog should not see `source` or `mat_version` unless they are relevant; an extended/admin response schema can expose them.
- **Update uniqueness constraints** to reflect the new shape: `(datastack, name, mat_version, revision)` still works, but `mat_version` is only populated when `source="materialization"`.
- **Simplify the validation pipeline** — `check_mat_table` reads `name` and `mat_version` directly from the asset instead of digging into `properties`.

## Capabilities

### New Capabilities
- `mat-source-flag`: Introduce a `source` enum field that cleanly marks assets as materialization-originated, restricted to service-level callers and hidden from regular users.
- `tiered-response-schemas`: Provide different response schemas (standard vs. admin/service) so materialization-internal fields are invisible to average users.

### Modified Capabilities

## Impact

- **Database**: Migration to drop `mat_version` column and add `source` column (or repurpose `mat_version` with new semantics). Existing rows need a data migration to infer `source` from `properties.source`.
- **API**: Breaking change to `AssetRequest` and `AssetResponse` — clients currently passing `mat_version` at the top level or in `properties` must update.
- **Validation**: `check_mat_table` and `check_name_reservation` simplified to use top-level fields.
- **Auth/middleware**: New logic to gate `source="materialization"` behind service-level identity.
- **CAVEclient / MaterializationEngine**: Any callers that register materialization assets need to update to the new interface.
