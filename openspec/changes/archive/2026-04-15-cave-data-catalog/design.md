## Context

CAVE annotation data is currently only accessible through the MaterializationEngine's REST query API, backed by PostgreSQL. Researchers increasingly want to work with this data using analytical tools (DuckDB, Polars) and to share derived feature datasets (embeddings, morphology metrics) that don't fit into the existing annotation/materialization model. Today, data sharing happens via ad-hoc bucket paths with no discovery, access control, or formal linkage back to CAVE entities.

The stack is Python-first (Flask/FastAPI), runs on GKE, uses PostgreSQL for state, and delegates authentication to middle_auth via middle_auth_client decorators. CAVE-managed cloud storage is on GCS, but some public/external datasets reside on AWS S3, and future CAVE deployments may use S3 as well.

## Goals / Non-Goals

**Goals (this change — Phase 0):**
- Provide a central registry for discovering what data assets exist for a given datastack and version
- Integrate with the existing CAVE stack: middle_auth for auth, CAVEclient for programmatic access. Include enough metadata about materialization / chunkedgraph that linking is possible.
- First-class support for some initial tabular data formats (Delta Lake, Lance, Parquet). See below about future support for other formats.
- Where possible, avoid becoming authoritative over format-specific metadata (schema, partitioning, etc.) since this is often advertised by the format (e.g. _delta_log, Neuroglancer info)
- Design incrementally: each phase is independently useful

**Deferred to separate changes:**
- Credential vending (prefix-scoped cloud storage tokens) → `catalog-credential-vending`
- View definitions (SQL templates + asset references) → `catalog-view-definitions`
- Production deployment (Helm, Terraform, AFIS) → `catalog-deployment`

**Non-Goals:**
- Query execution — the catalog never runs queries; DuckDB/Polars do that client-side
- Schema authority — the data format (Delta log, info file, etc.) is authoritative for schema; the catalog only caches hints
- Data writing — the catalog does not write to data buckets; producers write independently and then register
- Replacing MaterializationEngine's query API — the catalog serves static dumps, not live queries. 
- Per-row or per-cell lineage tracking — the catalog describes asset-level and column-level relationships, not row-level references
- Full Iceberg REST Catalog or Delta Sharing protocol compliance (can be added later as additive endpoints)

**Possible Future Goals:**
- Door open to future compatibility with Iceberg, Neuroglancer precomputed, skeletons, or other cloud bucket assets.
- However, a future CAVEclient convenience layer that provides a materialization-compatible query interface on top of catalog-hosted table dumps (e.g., using Polars to mimic `client.materialize.query_table()`) should be straightforward to build. This is not part of the catalog service itself — it is a client-side concern that the catalog's design must not preclude.
- Further separation of concerns where materialization service handle bound spatial points and root ID updating, catalog handling the backend of tabular storage.

## Decisions

### 1. Custom thin service over Unity Catalog OSS or other off-the-shelf catalogs

**Decision**: Build a custom FastAPI + PostgreSQL service (~500-1000 lines).

**Alternatives considered**:
- **Unity Catalog OSS**: JVM-based, no GCS credential vending (AWS S3 only), has its own user/permissions system that would duplicate middle_auth, no view support in OSS, and version isn't a first-class namespace dimension. The things UC gives for free (table CRUD, namespace, properties) are the easy parts to build; the things it lacks (GCS creds, middle_auth, views, CAVE lineage) are the hard parts that must be custom regardless.
- **Nessie / Polaris**: JVM-based Iceberg catalog servers. Add Git-like branching (Nessie) or pure Iceberg REST catalog (Polaris). Both are heavier than needed and don't solve credential vending for GCS or middle_auth integration.
- **Delta Sharing protocol**: Good fit for Delta-only credential vending (DuckDB has native connector), but no view spec, and the reference implementation is JVM-based. Could be added later as an additive endpoint.

**Rationale**: The total custom code is comparable to what's needed to fork/extend any off-the-shelf solution, and it stays in the Python/FastAPI stack the team already operates.

### 2. Format-agnostic asset registry with format as truth

**Decision**: The catalog stores discovery, lineage, permissions, and credential metadata. The data format itself (Delta log, Neuroglancer info file, Parquet footer) remains authoritative for schema, partition info, and statistics. The catalog may cache "hints" (row count, column names) for browsing but marks them as non-authoritative.

**Rationale**: Avoids dual-source-of-truth drift. Every consumer already reads format-native metadata at query time (DuckDB reads the Delta log, Neuroglancer reads the info file). Duplicating it in the catalog creates maintenance burden with no benefit.

### 3. Data model: `(datastack, name, mat_version, revision)` as natural key

**Decision**: Single `assets` table in PostgreSQL. The natural key is `(datastack, name, mat_version, revision)` where `mat_version` is a nullable integer (the CAVE materialization version, if applicable) and `revision` is a non-null integer defaulting to 0 (the asset's own iteration, 0-indexed — the first upload is revision 0). Because PostgreSQL treats NULLs as distinct in UNIQUE constraints, uniqueness is enforced via two partial unique indexes:

```sql
CREATE UNIQUE INDEX assets_unique_with_mat
  ON assets (datastack, name, mat_version, revision)
  WHERE mat_version IS NOT NULL;

CREATE UNIQUE INDEX assets_unique_without_mat
  ON assets (datastack, name, revision)
  WHERE mat_version IS NULL;
```

Additional top-level fields beyond the natural key: `mutability` (enum: `"static"` or `"mutable"` — whether the underlying data may change after registration) and `maturity` (enum: `"stable"`, `"draft"`, or `"deprecated"` — readiness for consumption). All other CAVE-specific metadata (source, lineage, descriptions, tags, cached hints) lives in a `properties` JSONB column.

**Alternatives considered**:
- Single `version` field (conflates mat version with asset revision) — rejected because "version" already has a strong meaning in CAVE (materialization version), and a single field can't represent "revision 2 of synapse_embeddings at mat version 943."
- `mat_version` as a non-nullable field with sentinel value (e.g., -1) — avoids the partial index complexity but introduces a magic number convention.
- Separate tables for lineage, references, etc. — premature normalization; the metadata is small and JSONB is easily filtered with GIN indexes.

**Physical layout variants**: The same logical dataset may exist in multiple physical layouts (partitioning, sort order, indexing) optimized for different query patterns — e.g., synapses partitioned by `pre_pt_root_id` vs `post_pt_root_id` vs spatial position. These are not revisions (none is "better"), not different data, and not well-captured by a single field (a layout may involve many storage decisions). Layout variants are encoded in `name` via convention (`synapses.by_pre_root`) with `properties.base_name` linking to the logical dataset name and `properties.layout` providing structured metadata (partition columns, sort order, description). This keeps the natural key simple while enabling programmatic discovery (`WHERE properties->>'base_name' = 'synapses'`). Views reference the appropriate layout for their query pattern. The exact naming convention for layout suffixes is listed under Naming Conventions below.

### 4. Cloud-agnostic credential vending with provider-specific backends

**Decision**: The credential vending API is cloud-agnostic — it returns a short-lived, prefix-scoped token regardless of storage provider. The backend implementation is provider-specific behind a common interface: GCS uses Credential Access Boundaries (downscoped OAuth tokens), S3 will use STS AssumeRole with inline policy scoped to a prefix. The asset's URI scheme (`gs://` vs `s3://`) determines which backend to use. GCS is implemented first; S3 support is added when needed.

**Alternatives considered**:
- Per-file signed URLs — requires enumerating all files in a table (must read Delta log server-side), scales poorly, and is format-aware. Prefix-scoped tokens are format-agnostic.
- Long-lived service account keys / IAM access keys — security risk, not time-bounded.
- GCS-only design — would exclude public S3 datasets and future S3 deployments.

**Rationale**: Prefix-level tokens are format-agnostic (client reads whatever files exist under the prefix), require one token per request (not per file), and are natively supported by cloud storage client libraries that DuckDB/Polars use. Abstracting behind a provider interface costs little now and avoids a redesign later.

### 5. Permissions: datastack-level with clean upgrade path

**Decision**: Phase 0 uses datastack-level permissions inherited from middle_auth. A nullable `access_group` column enables per-asset permissions later: when NULL, fall back to datastack permissions; when set, check membership in that middle_auth group.

### 6. Materialization table name reservation

**Decision**: Asset names that match a current materialization table name (for the same datastack) are reserved. Registration of a reserved name requires an elevated permission — specifically, setting `properties.source` to `"materialization"` requires admin/service-level write permission. Regular users who attempt to register an asset whose name matches a mat table are rejected with a descriptive error. The check queries the MaterializationEngine `/tables` endpoint (union across all versions for the datastack) at registration time. Layout variants of reserved names (e.g., `synapses.by_pre_root` when `synapses` is a mat table) are also reserved.

**Rationale**: Prevents namespace confusion where a user's external feature table shadows an official materialization dump. Using `properties.source` as the gate (rather than a separate endpoint or service account check) keeps the API surface simple and the authorization logic in one place.

**Concern — reverse collision**: The catalog cannot prevent someone from creating a new annotation table in AnnotationEngine that happens to share a name with an existing catalog asset or view. If that table is later materialized, the mat dump service would register it and collide with the existing catalog entry. Mitigation options include: (a) a cross-check during annotation table creation (coupling AnnotationEngine to the catalog), (b) accepting the collision and letting the mat dump service overwrite/coexist, or (c) a naming convention that partitions the namespace (e.g., external assets must use a prefix like `ext.`). No mitigation chosen yet — flagged for group discussion.

### 7. Synchronous validation at registration

**Decision**: Registration uses synchronous validation (dedup check, auth check, URI reachability via HEAD request, format sniff by reading format-specific metadata like `_delta_log/`). Source-conditional validation: mat dumps also verify the claimed mat table/version exists via MaterializationEngine API. Total expected time: under 10 seconds.

**Rationale**: Registration is low-frequency (a few times a day). Synchronous validation is simpler than async and provides immediate feedback. Async batch registration can be added later if volume increases.

### 8. Views as SQL templates resolved client-side, with `latest` references

**Decision**: A view is a stored SQL template with named placeholders referencing other assets via the natural key path syntax: `datastack/name/mat_version/revision`. The special keyword `latest` may be used in place of `mat_version` and/or `revision` to resolve to the highest available value at query time. For example, `minnie65_public/synapses/latest/latest` resolves to the latest revision of synapses at the latest mat version. The `/resolve` endpoint resolves `latest` references, vends credentials, substitutes placeholder names with credential-vended URIs, and returns the ready-to-execute SQL string along with the concrete resolved references (so the caller knows exactly which assets were used).

**Alternatives considered**:
- UUID-only references — unambiguous but opaque (can't read the view and know what it points to) and not parameterizable.
- Server-side query execution — heavy, requires running a query engine. Against the design principle.
- Iceberg view spec — not available for Delta Lake; Delta Sharing has no view concept.
- Materialized views (pre-joined tables) — users can do this themselves by executing a query and uploading the result.

### 9. Phased delivery

- **Phase 0** (**this change**): Asset registry + discovery + synchronous validation + CAVEclient Phase 0 methods
- **Phase 1** (`catalog-credential-vending`): Credential vending + middle_auth gating on access
- **Phase 1.5**: Structured CAVE references (CAVE entity type + column mapping)
- **Phase 2** (`catalog-view-definitions`): View definitions + resolution
- **Deployment** (`catalog-deployment`): Helm chart, Terraform, AFIS integration

**Possible extensions:**
- Unified query interface for materialization/catalog in CAVEclient
- Broader asset types (meshes, skeletons, precomputed annotations), TTL/lifecycle — not committed; depends on whether the need is clear after Phases 0–2 are in use

## Risks / Trade-offs

- **[Properties JSONB becomes a bag of unvalidated junk]** → Establish conventions early (documented property names like `source`, `description`, `tags`, `pcg_graph`). Promote commonly-used fields to structured validation in Phase 1.5.
- **[Cloud credential vending varies by provider]** → GCS uses Credential Access Boundaries (downscoped OAuth), S3 uses STS AssumeRole. Test each provider's propagation delay and edge cases during Phase 1 development. Abstract behind a common interface so adding a provider doesn't change the API contract.
- **[View SQL templates are a potential injection surface]** → Views are registered by authorized writers only (not end-user-supplied SQL). Validate that templates only contain asset placeholders, not arbitrary code. Client-side execution means the blast radius is limited to the user's own DuckDB session.
- **[Catalog becomes stale if producers forget to register]** → Provide a bulk registration CLI/script for MaterializationEngine to run post-dump. Consider a periodic bucket scanner in Phase 3 to detect unregistered assets.
- **[Format sniff validation adds coupling to format-specific libraries]** → Keep format sniffing minimal (check for `_delta_log/`, `info` file, etc.) rather than deep parsing. Accept that some validation is best-effort.

## Open Questions

- **Single database vs per-datastack databases**: MaterializationEngine and AnnotationEngine both use a separate PostgreSQL database per aligned volume (datastack), with `DatabaseConnectionManager.get_engine(database_name)` routing requests. Adopting the same pattern for the catalog would drop `datastack` from the natural key (simplifying to `(name, mat_version, revision)`) and provide operational consistency. However, the catalog's schema is trivially simple (one table) and doesn't benefit from isolation the way mat/anno's dynamic per-table schemas do. Per-datastack DBs also add connection pool management and complicate cross-datastack views. Current decision: single database with `datastack` as a column. Revisit if operational pain emerges or if the team prefers uniformity.
- **TTL lifecycle scope**: Should the catalog only stop advertising expired assets (soft delete), or also trigger deletion of underlying bucket data for managed assets? Leaning toward catalog-only with cloud-native lifecycle rules (GCS Object Lifecycle, S3 Lifecycle) handling storage.
- **Deployment**: Own Cloud SQL instance or shared with another service? Own Helm chart is assumed.

## Naming Conventions (to discuss as a group)

- **Service name**: "catalog" vs "registry" vs "manifest" — "catalog" implies browsing and discovery but "data catalog" is slightly overloaded and has a specific meaning at the Allen Institute; "registry" implies registration and lookup but "register" is overloaded in connectomics (image registration); "manifest" (a list of cargo with metadata) fits the thin-metadata-layer design and avoids both collisions but is less standard.
- **`mat_version` vs `materialization_version`**: The field name in the data model. `mat_version` is shorter and matches common CAVE shorthand. `materialization_version` is more explicit and self-documenting for newcomers. The existing MaterializationEngine API uses `version` in its paths.
- **Layout variants**: Convention for naming physical layout variants of the same logical dataset. Current proposal: `name.layout_suffix` (e.g., `synapses.by_pre_root`, `synapses.spatial`) with `properties.base_name` linking back to the logical dataset name. Alternatives: separate `layout` field, hierarchical names (`synapses/by_pre_root`), or flat distinct names with a tag/property for grouping.

## Future Considerations

- **Materialization-compatible query interface**: A future CAVEclient convenience layer that mimics `client.materialize.query_table()` on top of catalog-hosted table dumps, using an opinionated engine (e.g., Polars) without locking users into that choice.
- **Layout-aware query routing**: A future API helper ("give me the best layout of synapses for this query pattern") that recommends or auto-selects the optimal layout variant based on query predicates. Not planned for initial phases.
