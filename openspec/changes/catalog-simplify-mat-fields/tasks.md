## 1. Schema & Model Changes

- [ ] 1.1 Add `Source` StrEnum (`"user"`, `"materialization"`) to `schemas.py`
- [ ] 1.2 Add `source` column (String, NOT NULL, default `"user"`) to the `Asset` ORM model in `db/models.py`
- [ ] 1.3 Add `source` field (optional, default `"user"`) and keep `mat_version` (optional) on `AssetRequest`
- [ ] 1.4 Create `AssetResponse` (standard: omits `source` and `mat_version`) and `AssetDetailResponse` (admin: includes them) in `schemas.py`
- [ ] 1.5 Create Alembic migration: add `source` column with default `"user"`, backfill from `properties->>'source'` for existing rows

## 2. Admin-Gating Logic

- [ ] 2.1 Add helper function in the router (or a dependency) to sanitize incoming `AssetRequest`: if caller is not admin, set `source="user"` and `mat_version=None`, log warning if fields were dropped
- [ ] 2.2 Wire the sanitizer into `register_asset` and `validate_asset` endpoints

## 3. Tiered Response Schemas

- [ ] 3.1 Update `_asset_to_response` to accept an `is_admin` flag and return `AssetDetailResponse` or `AssetResponse` accordingly
- [ ] 3.2 Update `list_assets`, `get_asset`, and `register_asset` endpoints to pass `user.is_admin` and return the appropriate schema
- [ ] 3.3 Silently ignore `mat_version` query filter in `list_assets` for non-admin callers

## 4. Validation Pipeline Refactor

- [ ] 4.1 Update `run_validation_pipeline` signature to accept `source` and `mat_version` as explicit parameters instead of reading from `properties`
- [ ] 4.2 Update `check_mat_table` call site: pass `name` as the source table, `mat_version` from the top-level field; only invoke when `source="materialization"`
- [ ] 4.3 Remove `properties.source`, `properties.source_table`, `properties.mat_version` special-casing from `check_name_reservation` and `run_validation_pipeline`

## 5. Tests

- [ ] 5.1 Test: non-admin registration silently drops `source` and `mat_version`
- [ ] 5.2 Test: admin registration preserves `source="materialization"` and `mat_version`
- [ ] 5.3 Test: non-admin list/get responses omit `source` and `mat_version` keys
- [ ] 5.4 Test: admin list/get responses include `source` and `mat_version`
- [ ] 5.5 Test: non-admin `mat_version` query filter is ignored in list endpoint
- [ ] 5.6 Test: validation pipeline skips mat verification when `source="user"` regardless of properties content
- [ ] 5.7 Test: validation pipeline runs mat verification using `name` and top-level `mat_version` when `source="materialization"`
- [ ] 5.8 Test: warning logged when fields are silently dropped
