## Context

The catalog service stores metadata about data assets across CAVE datastacks. Currently, materialization-sourced assets carry redundant fields:

- **Top-level `mat_version`** on the `Asset` model and request/response schemas
- **`properties.mat_version`** and **`properties.source_table`** in the freeform JSON blob
- **`properties.source`** (`"materialization"`) as an ad-hoc convention to flag materialization origin

The validation pipeline (`check_mat_table`) reads from `properties` while the ORM model stores `mat_version` at both levels. The asset `name` effectively *is* the source table for materialization assets, but nothing enforces that today.

The auth layer uses `AuthUser` with `is_admin`, `permissions` (resource→actions mapping), and `groups`. There is no explicit service-account concept yet.

## Goals / Non-Goals

**Goals:**
- Single source of truth: `mat_version` lives in one place on the model (top-level), and `name` *is* the source table name for materialization assets — no parallel fields in `properties`.
- A first-class `source` field (not an ad-hoc property key) with enum values that clearly declares asset provenance.
- Service-only gating: only callers identified as a service (e.g., MaterializationEngine) can set `source="materialization"`.
- Regular users never see or interact with `source` or `mat_version` — those fields are omitted from the standard response and rejected if passed in a normal user request.

**Non-Goals:**
- Full service-account / OAuth scope system — we'll use the simplest viable gating mechanism.
- Changing how the validation pipeline calls MaterializationEngine APIs.
- Migration of historical data at this stage (migration strategy is noted but deferred).

## Decisions

### 1. Keep `mat_version` as a top-level column, remove it from `properties`

**Choice**: `mat_version` stays as a column on `Asset`; `properties.mat_version` and `properties.source_table` are eliminated.

**Why**: `mat_version` participates in uniqueness constraints and query filters — it belongs in the relational schema, not loose JSON. Removing the `properties` copies eliminates the consistency hazard without losing any queryability.

**Alternative considered**: Move `mat_version` *into* `properties` and index it with a generated column. Rejected because it adds Postgres-specific complexity for a field that is clearly relational.

### 2. Add `source` enum column with values `"user"` (default) and `"materialization"`

**Choice**: A new `source` column (String, NOT NULL, default `"user"`) replaces `properties.source`. The column value controls validation behavior and response shaping.

**Why**: Making `source` a first-class column lets us use it in SQL filters, enforce constraints, and branch logic cleanly. An enum at the Pydantic level (`StrEnum`) prevents typos.

**Alternative considered**: A boolean `is_materialization`. Rejected because `source` is more extensible if other provenance types appear later (e.g., `"derived"`, `"imported"`).

### 3. Gate `source="materialization"` via `is_admin` on `AuthUser`

**Choice**: Only callers with `is_admin=True` can set `source="materialization"`. The API silently ignores `source` and `mat_version` when sent by non-admin callers (rather than returning an error), so that a single schema can be used internally while average users simply don't see those fields.

**Why**: The auth layer already plumbs `is_admin` from the auth service response (set when `admin` or `superadmin` is true). Service tokens for MaterializationEngine are typically admin-scoped. This avoids adding a new permission concept.

**Alternative considered**: A dedicated `"service"` permission or a separate `/internal/register` endpoint. A separate endpoint would be cleaner long-term but is heavier than needed now; we can introduce it later if the admin-gate proves insufficient.

### 4. Two response schemas: `AssetResponse` (standard) and `AssetDetailResponse` (admin/service)

**Choice**: `AssetResponse` omits `source` and `mat_version`. A new `AssetDetailResponse` includes them. The list/get endpoints return the detail schema only for admin callers; standard users get the trimmed schema. Alternatively, a single schema with `Exclude`/`Optional` fields could work, but two schemas are more explicit in OpenAPI docs.

**Why**: Average users have no reason to see these fields. Hiding them keeps the API surface clean and avoids confusion about fields they cannot set.

### 5. `AssetRequest` keeps `mat_version` and adds `source`, both ignored for non-admins

**Choice**: The request schema retains `mat_version` (optional) and adds `source` (optional, default `"user"`). When a non-admin caller sends `source="materialization"` or a `mat_version`, the server silently drops them (sets `source="user"`, `mat_version=None`). This is simpler than maintaining two request schemas.

**Why**: A single request schema avoids needing a separate internal endpoint. Silent ignore (rather than 403) means client code doesn't need to branch — the MaterializationEngine can always send these fields, and a human caller's request just works without them.

### 6. Validation pipeline reads top-level fields

**Choice**: `run_validation_pipeline` accepts `source` and `mat_version` as explicit parameters instead of extracting them from `properties`. `check_mat_table` uses `name` (the asset name) as the table name when `source="materialization"`.

**Why**: Direct parameter passing is clearer, type-safe, and removes the hidden coupling to `properties` keys.

## Risks / Trade-offs

- **Breaking change for existing API callers** → Mitigated by keeping `mat_version` on the request schema and silently ignoring it for non-admins. Existing callers sending `properties.source` or `properties.source_table` will need to update, but the fields were ad-hoc conventions — no stable contract existed.
- **Admin-gate is coarse** → Any admin can set `source="materialization"`, not just the MaterializationEngine. Acceptable for now; a dedicated service identity or internal endpoint can tighten this later.
- **Data migration needed for existing rows** → Existing assets with `properties.source="materialization"` need their new `source` column populated. A one-time Alembic migration can do this. Risk: if `properties` data is inconsistent (e.g., `properties.mat_version` ≠ top-level `mat_version`), the migration script needs a conflict resolution strategy. We default to trusting the top-level `mat_version`.
- **Silent ignore vs. explicit rejection** → Silently dropping `source`/`mat_version` for non-admins could surprise a caller who expects them to be set. Trade-off: cleaner UX for the common case (users who don't know these fields exist). Log a warning when fields are dropped so operators can diagnose issues.

## Open Questions

- Should the admin-gate be a separate permission (e.g., `"catalog:mat_register"`) instead of piggy-backing on `is_admin`? This would be more granular but requires auth service changes.
- Should we version the API (`/api/v2/assets/`) for the breaking schema changes, or is the service new enough that a clean break on v1 is acceptable?
