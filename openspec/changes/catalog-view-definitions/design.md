## Context

The catalog service has asset registration, discovery, and credential vending (Phase 1). Researchers currently write ad-hoc SQL joining multiple catalog assets, manually looking up URIs and constructing scan expressions. The parent design (cave-data-catalog) established Decision 8: views as SQL templates resolved client-side, with `latest` references.

This change depends on credential-vending being complete — the resolve endpoint vends credentials for each referenced asset.

## Goals / Non-Goals

**Goals:**
- Allow registering view assets with SQL templates and named references to other catalog assets
- Resolve `latest` keywords in references to concrete `mat_version`/`revision` values at query time
- Vend credentials for all referenced assets in a single resolve call
- Substitute placeholder names with format-appropriate scan expressions (e.g., `delta_scan(...)`)
- Return everything the client needs to execute the query locally

**Non-Goals:**
- Server-side query execution — the catalog never runs queries
- Materialized views — users can create those by executing a query and registering the result
- View dependency graphs or cascading updates — views are static templates
- Supporting non-SQL dialects initially (extensible via `dialect` field later)

## Decisions

### 1. View definition stored in `properties.definition`

**Decision**: View metadata lives in the existing `properties` JSONB column under a structured `definition` key containing `dialect`, `query`, and `references`. No schema migration needed.

**Alternatives considered**:
- Separate `view_definitions` table — cleaner normalization but premature for what may be a handful of views initially.
- Top-level `definition` column on the assets table — couples the schema to a specific asset type.

**Rationale**: Reuses the existing JSONB properties column. The validation layer enforces the schema for `asset_type: "view"` at registration time.

### 2. Reference path syntax: `datastack/name/mat_version/revision`

**Decision**: References use the natural key path format with `/` separators. The `latest` keyword resolves to the highest available value. Examples:
- `minnie65_public/synapses/943/0` — pinned reference
- `minnie65_public/synapses/latest/latest` — resolves to latest mat_version and revision
- `minnie65_public/embeddings/latest/0` — latest mat_version, revision 0

**Rationale**: Human-readable, matches the natural key, and the `latest` keyword avoids hardcoding version numbers that change with every materialization run.

### 3. Format-appropriate scan expressions

**Decision**: The resolve endpoint maps `format` → scan expression template:
- `delta` → `delta_scan('{uri}')`
- `parquet` → `read_parquet('{uri}')`
- `lance` → (future, via DuckDB lance extension)

The client can override or customize these via `to_duckdb_sql()`.

**Rationale**: DuckDB is the primary execution engine for CAVE analytics. Keeping the mapping simple and format-based avoids over-engineering.

### 4. Pinned references validated at registration; `latest` validated at resolve time

**Decision**: When a view is registered, pinned references (no `latest` keyword) are validated to ensure the referenced asset exists. References containing `latest` are not validated at registration — they are resolved at query time, and the resolve endpoint returns an error if no matching asset is found.

**Rationale**: `latest` references are intentionally dynamic. Validating them at registration would be misleading (the latest version will change).

## Risks / Trade-offs

- **[SQL template injection]** → Views are registered by authorized writers only. Validate that templates contain only known placeholders, not arbitrary code. Blast radius is limited to the user's own DuckDB session.
- **[Stale `latest` references]** → If the latest asset is deleted or expires, resolve returns a clear error. No silent fallback.
- **[Multi-asset resolve latency]** → Resolving N references means N credential vend calls. For now N is small (2–5 assets per view). Can parallelize the vend calls if latency matters.
