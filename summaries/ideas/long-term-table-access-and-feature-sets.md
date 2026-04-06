# CAVE Data Catalog — Design Document

**Date:** 2026-04-06  
**Status:** Draft

---

## 1. Problem Statement

Two capability gaps share three identical sub-problems: **discoverability**, **access control**, and **partial query**.

### Gap A — Long-term materialization dumps

MaterializationEngine creates frozen snapshots, but "long-term" quarterly dumps are exported as gzipped CSVs into a GCS bucket. These CSVs are:
- **Undiscoverable:** no machine-readable index; users learn about them by word of mouth.
- **Coarsely gated:** bucket-level IAM only; no integration with middle_auth.
- **All-or-nothing:** retrieving a subset of a 10s-of-GB table requires downloading the entire file.

### Gap B — Feature sets (embeddings, morphology descriptors, etc.)

Data scientists produce tabular features keyed to PCG root IDs. These are not "annotations" and don't belong in AnnotationEngine, yet they suffer the same three problems as Gap A, with the added complication that their computation schedule may not align with materialization versions.

### Unifying observation

Both gaps require data to be **linked to a specific materialization version** and both need a discoverability + access + partial-query layer. A single design — a *catalog service* — can serve both.

---

## 2. Design Principles

These principles are extracted from the analysis below and should guide implementation decisions:

1. **Format-authoritative metadata.** The storage format (Iceberg, Lance, etc.) is the source of truth for column names, types, and statistics. The catalog stores only what the format cannot: semantic roles, lineage, access tier, and datastack membership.
2. **Proxy the credential, not the data.** The catalog service handles auth and metadata; users query data directly from object storage via short-lived signed URLs. No data passes through the service on the hot path.
3. **Thin registry, not a query engine.** The catalog is a metadata registry. Query execution belongs to client-side tooling (DuckDB, Polars) or a future server-side engine — not the catalog service itself.
4. **Incremental scope.** Start with tabular data only. Non-tabular assets (meshes, precomputed volumes) and logical views are future extensions, not MVP requirements.

---

## 3. Architecture

### 3.1 Catalog service

A new Flask service backed by Postgres, following the standard CAVE service pattern (`middle_auth_client` for auth, CAVEclient sub-client wrapper). Deployed as its own Helm chart, Docker image, and Postgres database, with corresponding `terraform-google-cave` additions.

**Why a new service instead of extending AnnotationFrameworkInfoService:**
- AFIS is a config registry (datastacks, aligned volumes, permission groups). Catalog records are versioned data assets with column-level metadata, lineage DAGs, and per-resource ACLs — a fundamentally different data model.
- Feature sets come from many sources (different labs, pipelines). A dedicated service gives external contributors a well-scoped registration API without complicating AFIS.
- Per-catalog-record ACLs (backed by middle_auth groups) require finer granularity than AFIS's datastack-level permission model.

### 3.2 Data model

#### Catalog record fields

**Core identity:**
- Datastack name
- Logical table name (stable identifier grouping all versions/variants)
- Materialization version (FK to materialization version)
- Variant tag (free-form; distinguishes same-version physical variants, e.g. "partitioned by pre_root_id" vs. "partitioned by post_root_id")

**Storage:**
- Storage format (`iceberg` | `lance` | `parquet`)
- Bucket path / catalog URI
- `access_type` (`managed` | `open`)

**Semantic metadata (catalog-owned):**
- Semantic role annotations per column (`root_id` = PCG join key, etc.)
- Joinable key columns and references to related annotation tables/columns
- Table family tag (optional grouping, e.g. all synapse variants)

**Lineage:**
- `derived_from` (pointer to source catalog record or annotation table)
- `superseded_by` (pointer to replacement record)
- Pipeline/model version (for feature sets)

**Curation:**
- Status: `draft` | `published` | `deprecated`
- Ownership (team or individual)
- Tags / categories (free-form)

**Optional / future:**
- Citation, licensing constraints, retention policy / TTL
- Invalidation radius (for spatial features)

> **[FEEDBACK — Schema layering]** The flat field list above mixes MVP-critical fields with nice-to-haves. Before implementation, split this into required-for-v1 (identity + storage + lineage + status) vs. optional-for-v1 (citation, licensing, retention, invalidation radius). A 20-field registration form will deter contributors.

**Cached (display-only):**
- Schema snapshot (column names + types read from the format at registration time), with `schema_last_synced_at` timestamp. If the snapshot drifts from the format, the record is flagged stale.

#### Versioning model

A single logical table (e.g. "synapses for flywire_783") will have many catalog records — across time (materialization v1400, v1503, …) and potentially across physical variants at the same timepoint. The identity model is:

```
(datastack, logical_table_name) → stable identity
  + materialization_version      → temporal versioning
  + variant_tag                  → same-version physical variants
```

Query interfaces (`client.catalog.list()`) must make it easy to discover all variants for a logical table and filter by version or variant tag.

#### Record immutability

> **[FEEDBACK — Missing design decision]** Can a `published` catalog record be mutated after creation? If the underlying data is re-exported with a bug fix, does the old record get deprecated and a new one created, or is the record updated in place? Immutable-once-published is strongly preferable for reproducibility — mutations should be modeled as deprecate-and-replace. This should be an explicit rule.

### 3.3 Access control

The catalog service proxies credentials, not data. Two access tiers:

**Managed** (CAVE-owned bucket): the catalog service verifies the user against middle_auth, then issues **short-lived signed URLs** (or STS / Workload Identity Federation tokens) scoped to the relevant bucket prefix. The user queries Iceberg/Lance directly from their local environment.

**Open** (public or externally-owned bucket): the catalog returns the URI directly. Value is purely discoverability and schema metadata.

#### Curation and write-access gatekeeping

Registration requires an elevated permission (e.g. a `catalog:write` middle_auth group), separate from general datastack read access. The `status` field (`draft` → `published` → `deprecated`) provides a lightweight curation workflow. Even open resources require authenticated registration to prevent spam.

#### Open considerations

- **External-but-not-public resources:** a collaborating lab's bucket accessible via a shared service account is neither "managed" nor "open." A third `access_type: delegated` could store a service-account reference, but this makes the catalog a credential broker — a significant security surface. **Recommend deferring this to v2** and requiring external contributors to either make data public or grant read access to CAVE's service account (making it effectively "managed").
- **Signed URL security model:** TTL, scope (per-object vs. per-prefix), signing key rotation, and URL sharing risks need specification before implementation. GCS signed URLs support expiry but not byte-range limits, so abuse mitigation requires catalog-layer rate limiting or per-user quotas.
- **Egress cost:** signed URLs grant potentially unbounded reads on large tables. Mitigations: tiered storage (Nearline/Coldline) for infrequently accessed tables, access logging via the catalog service, and per-user download quotas if costs become material.
- **External link stability:** catalog records pointing to external storage should carry a content hash so staleness is detectable.

### 3.4 Storage formats

| Format | Use case | Rationale |
|--------|----------|-----------|
| **Apache Iceberg** (parquet-snappy) | Long-term mat dumps, flat-tabular feature sets | Broadest ecosystem (DuckDB, Spark, BigQuery external tables, Polars, PyIceberg). Strong partition pruning via column stats in metadata. |
| **Lance** | Feature sets requiring ANN similarity search | Native approximate nearest-neighbor index. Only justified if vector similarity queries are a real use case. |

Partition long-term mat dumps by root ID range.

> **[FEEDBACK — Format complexity vs. benefit]** Supporting both Iceberg and Lance doubles the adapter, testing, and documentation surface. The primary access pattern described throughout this document is "join by root_id" — which Iceberg handles perfectly. Unless there are concrete, near-term user stories for ANN similarity search on embeddings, **start with Iceberg only** and add Lance support when a real use case materializes. "We might want ANN someday" is not sufficient justification for day-one complexity.

> **[FEEDBACK — Iceberg catalog backend is a blocking decision]** Iceberg tables require a catalog (in the Iceberg sense — resolves table names to metadata file locations). Options: REST catalog (self-hosted), Hadoop-style file I/O catalog (simpler, uses convention-based paths on GCS), Nessie, or BigLake Metastore (GCP-managed). This is called out in Open Questions but is actually a blocking architecture decision that determines deployment topology. Elevate this to a design decision and resolve it before implementation. The file I/O catalog (`HadoopCatalog` equivalent) is simplest for MVP — no additional service to deploy — but loses table-level locking and atomic renames.

### 3.5 Schema metadata strategy

The format is authoritative for mechanical metadata. The catalog owns only semantic metadata:

| Owned by format | Owned by catalog |
|-----------------|------------------|
| Column names and types | Semantic roles (`root_id` = PCG join key, etc.) |
| Partition spec | Lineage (materialization version, pipeline version) |
| Column statistics | Access type, datastack membership |
| Nullability | Join key documentation |

At registration time, the catalog reads and caches a schema snapshot for display purposes (with a `schema_last_synced_at` timestamp). The format remains ground truth. EMAnnotationSchemas types can be referenced for known columns; arbitrary extras are allowed for embeddings and novel feature types.

---

## 4. Client Integration

A new `client.catalog` sub-client in CAVEclient:

### 4.1 Core operations

1. **`list(datastack, [table_name], [version], [tags])`** — discover available tables and feature sets, filterable by logical table, materialization version, variant tag, or free-form tags.
2. **`describe(datastack, table_name, version)`** — return format, semantic roles, lineage, and (cached) column schema.
3. **`open(datastack, table_name, version, [variant])`** — return a ready-to-query object (PyIceberg `Table`, Lance `Dataset`, etc.) backed by signed URLs. Example:
   ```python
   tbl = client.catalog.open("flywire_783", "synapses", version=1412)
   df = tbl.to_pandas(filter=pc.field("root_id").isin(my_ids))
   ```

### 4.2 Query helpers

4. **`query(datastack, table_name, version, root_ids=...)`** — thin wrapper for the common "give me rows for these root IDs" pattern.
5. **`enrich(df, tables=["bouton_size_v3", ...], version=1412)`** — join feature columns onto an existing DataFrame by root ID, using semantic role metadata to determine join keys:
   ```python
   synapses = client.materialize.synapse_query(post_ids=my_roots, materialization_version=1412)
   synapses = client.catalog.enrich(synapses, tables=["bouton_size_v3", "distance_from_soma"], version=1412)
   ```

> **[FEEDBACK — Data size and local execution]** `enrich()` implicitly downloads feature table partitions and joins them in-memory on the user's machine. For a synapse query returning millions of rows enriched with multiple feature tables, this could be tens of GB. The document should specify: (a) whether `enrich()` filters the feature tables server-side (via Iceberg partition pruning on root_id) before downloading, and (b) what happens when the joined result exceeds available memory. At minimum, document expected data sizes for the target use cases and validate that client-side join is practical.

> **[FEEDBACK — DuckDB as implicit dependency]** `open()` and `enrich()` likely require DuckDB (or PyIceberg + PyArrow) on the client side for Iceberg reads. This is a significant new dependency for CAVEclient users. Make this explicit — either as a required dependency or as an optional extras group (`pip install caveclient[catalog]`).

---

## 5. Build vs. Buy

The following tools were evaluated for the catalog backend:

| Tool | Strengths | Weaknesses for CAVE | Verdict |
|------|-----------|---------------------|---------|
| **Unity Catalog OSS** | REST API, schema tracking, Iceberg/Delta support, lightweight | No CAVE-specific semantic roles; no middle_auth integration; limited non-tabular support | Possible for tabular-only MVP; requires wrapping |
| **Project Nessie** | Git-like table versioning, Iceberg-native | Iceberg/Delta only; no general metadata model | Good Iceberg catalog backend, poor metadata registry |
| **OpenMetadata** | Custom entity types, connector framework, lineage | Heavy (requires Elasticsearch); enterprise-oriented | Over-engineered for CAVE's scale |
| **DataHub** | Flexible metadata model | Very heavy (Kafka + Elasticsearch) | Far more than needed |
| **Marquez** | Lightweight lineage tracking | Lineage only; no discoverability or schema features | Too narrow |

**Decision: build a custom lightweight service.** The registry itself is simple CRUD. What's novel is the CAVE-specific logic: middle_auth integration, semantic role metadata, EMAnnotationSchemas interop, and signed-URL issuance. Off-the-shelf tools would require as much wrapping and customization as building from scratch, while adding operational dependencies (Elasticsearch, Kafka) that CAVE doesn't otherwise need.

> **[FEEDBACK — Nessie as Iceberg catalog backend]** Even though Nessie isn't suitable as the metadata registry, it could serve as the *Iceberg catalog backend* (the thing that resolves table names to metadata.json locations). This is a different role than the CAVE catalog service — Nessie would sit between PyIceberg and GCS, while the CAVE catalog sits between users and Nessie. Worth evaluating separately from the build-vs-buy decision for the registry.

---

## 6. Boundary with MaterializationEngine

> **[FEEDBACK — Missing section]** The document doesn't specify the boundary between the catalog service and MaterializationEngine. ME already knows about materialization versions and currently writes the CSV dumps. Key questions:
> - Does ME trigger the Iceberg export and then call the catalog registration API? Or does the catalog service trigger ME to export?
> - Does ME's existing `MATERIALIZATION_UPLOAD_BUCKET_PATH` become the catalog's managed storage, or is there a separate bucket?
> - Does the catalog service need to query ME to validate that a materialization version exists and is marked "long-term"?
>
> The cleanest boundary is probably: **ME owns the export pipeline** (writes Iceberg tables to GCS), **catalog owns the registry** (ME calls the catalog registration API after a successful export). This keeps ME's existing responsibilities intact and avoids the catalog service needing to understand export mechanics.

---

## 7. Migration Path for Existing Data

> **[FEEDBACK — Missing section]** The document describes the future state (Iceberg/Lance tables) but doesn't address existing gzipped CSV dumps in GCS. Options:
> 1. **Convert existing CSVs to Iceberg** and register them in the catalog (one-time migration).
> 2. **Register CSVs as-is** with `format: parquet_csv_gz` or similar, accepting that they won't support partial query.
> 3. **Leave existing CSVs unregistered** and only catalog new exports going forward.
>
> Option 1 is the most valuable (existing data becomes queryable) but has a cost. At minimum, decide whether backward compatibility with existing dumps is a requirement.

---

## 8. Future Extensions (Explicitly Out of Scope for v1)

### 8.1 Non-tabular asset registry (adapter pattern)

Similar discoverability and lineage problems exist for Neuroglancer precomputed volumes, mesh layers, and flat segmentations. These objects self-describe their mechanical metadata (resolution pyramids, chunk sizes, coordinate spaces) in format-native files (e.g. Neuroglancer `info` files), analogous to how Iceberg embeds column schemas.

The catalog could generalize to non-tabular assets by introducing **adapters** — one per object type — that fetch and parse native metadata on demand. The catalog DB stores only URI, object type, lineage, and access tier; adapters hydrate mechanical metadata live.

| Object type | Native metadata location |
|-------------|--------------------------|
| Iceberg table | `.metadata.json` |
| Lance dataset | Embedded schema |
| Neuroglancer precomputed | `info` file |
| Neuroglancer mesh | `info` + shard index |

**Why defer this:**
- The tabular problem is concrete and urgent. Non-tabular assets are a "would be nice."
- Non-tabular assets overlap with AnnotationFrameworkInfoService's existing registration of image and segmentation sources. The document claims "they answer different questions," but in practice two services registering the same segmentation source will confuse operators and clients. The boundary needs careful design.
- Each adapter is a maintenance surface (format versioning, error handling for missing/moved objects, read access requirements).
- Scope expansion from "tabular data catalog" to "universal asset registry" dramatically increases the design, implementation, and testing surface.

**Recommendation:** build v1 as tabular-only. If the adapter pattern proves valuable, extend in v2 with a clear boundary agreement with AFIS.

### 8.2 Logical views

A catalog record could represent a **view** — a named SQL query over registered tables, executed locally by DuckDB.

**Options considered:**
- **Option A (recommended for now):** no views; the `enrich()` helper covers the dominant join-by-root-id use case. Users write their own SQL for anything more complex.
- **Option B (future):** SQL views stored in the catalog, executed locally by DuckDB. Discoverable and shareable. Requires issuing signed URLs for all source tables simultaneously (TTL coordination).
- **Option C (distant future):** server-side query engine (Trino, DuckDB server). Maximum interoperability but significant infrastructure.

Start with Option A. The upgrade path A → B → C is clean — SQL text stored in view records can later be handed to a server-side engine.

---

## 9. What's New vs. What's Reused

| Need | Reused | New |
|------|--------|-----|
| Auth enforcement | middle_auth + middle_auth_client | — |
| Discoverability | — | Catalog service + `client.catalog` |
| Schema metadata | EMAnnotationSchemas type vocabulary (for known columns) | Catalog semantic role annotations |
| Partial query | — | Iceberg tables on GCS + signed-URL issuance |
| Client access | Existing CAVEclient sub-client pattern | `client.catalog` sub-client |
| Mat version linking | MaterializationEngine version IDs | FK in catalog records |

---

## 10. Open Questions

### Blocking (must resolve before implementation)

1. **Iceberg catalog backend:** REST catalog (self-hosted), file I/O catalog (GCS path conventions), Nessie, or BigLake Metastore? Determines deployment topology and operational burden.
2. **ME ↔ catalog boundary:** does ME trigger Iceberg export and call the catalog registration API, or does the catalog service orchestrate exports? (See §6.)
3. **Registration validation strictness:** how much should the catalog validate on registration? Spectrum ranges from "store what the registrant provides" to "auto-detect schema, verify mat version exists and is long-term, validate annotation table references." More enforcement = higher catalog quality but more friction. Recommend: validate mat version exists + attempt schema read from data; defer annotation-table-reference validation.

### Important (should resolve before v1 GA)

4. **Feature set versioning misalignment:** when a feature set's computation schedule doesn't align with materialization versions, how is it linked? Options: pin to nearest version, store independently with a timestamp and a `compatible_versions` range, or require feature set producers to always target a specific mat version.
5. **Record immutability policy:** can a `published` record be mutated, or must changes go through deprecate-and-replace? (See §3.2.)
6. **Existing CSV migration:** convert, register as-is, or abandon? (See §7.)
7. **Signed URL TTL and scope:** what expiry, what prefix scope, how to handle URL sharing?

### Deferred (v2 and beyond)

8. **Non-tabular asset scope:** when and how to extend to meshes, precomputed volumes, etc., and what boundary with AFIS. (See §8.1.)
9. **Logical views:** when does `enrich()` become insufficient, triggering the need for stored views? (See §8.2.)
10. **`delegated` access type:** credential brokering for external-but-not-public resources. (See §3.3.)
11. **Server-side query execution:** Trino / DuckDB server as a future upgrade from client-side execution.
12. **Catalog-driven exports:** should the catalog service eventually trigger export jobs, or always remain a passive registry?

### Standing questions (from original brainstorm, still open)

13. **Automated pipeline integration:** the registration API should support machine registrants (e.g. Dagster workflows) from the start, including stable, collision-resistant naming for auto-registered tables.
14. **Durability tiers:** should `published` ("reference") tables have stronger durability guarantees than `draft` tables? How is this enforced — different storage classes, different retention policies?
15. **Egress cost management:** at what usage level do signed-URL-based reads become a cost problem, and what's the mitigation plan?

---

## 11. Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| **Scope creep into universal asset registry** | High | Delays v1 delivery, complicates AFIS boundary | Hard scope boundary: v1 = tabular only. Non-tabular is a separate design phase. |
| **Schema overload deters registration** | Medium | Low adoption; catalog becomes empty | Minimal required fields for v1. Optional fields can be backfilled. |
| **Two sources of truth (catalog vs. format)** | Medium | Stale metadata, user confusion | Format-authoritative principle (§2). Catalog caches schema snapshots for display only. |
| **Iceberg catalog backend choice locks in infrastructure** | Medium | Costly migration if wrong choice | Start with file I/O catalog (simplest); migrate to REST catalog if locking/atomicity becomes an issue. |
| **DuckDB dependency fragments CAVEclient user base** | Medium | Users without DuckDB can't use `open()`/`enrich()` | Optional extras group. Ensure `list()` and `describe()` work without DuckDB. |
| **Signed URL abuse / egress cost** | Low-Medium | Unexpected cloud bills | Access logging, per-user quotas, tiered storage for cold data. |
| **No user validation of `enrich()` pattern** | Medium | Build enrichment API that nobody uses as designed | Validate with 2-3 target users before committing to the `enrich()` interface. |
| **Feature set versioning misalignment** | High | Confusion about which feature set matches which mat version | Resolve design question #4 before implementation. |
