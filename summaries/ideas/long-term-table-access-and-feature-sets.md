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
- AnnotationFrameworkInfoService is a config registry (datastacks, aligned volumes, permission groups). Catalog records are versioned data assets with column-level metadata, lineage DAGs, and per-resource ACLs — a fundamentally different data model.
- Feature sets come from many sources (different labs, pipelines). A dedicated service gives external contributors a well-scoped registration API without complicating AnnotationFrameworkInfoService.
- Per-catalog-record ACLs (backed by middle_auth groups) require finer granularity than AnnotationFrameworkInfoService's datastack-level permission model.

### 3.2 Data model

#### Catalog record fields

**Core identity:**
- Datastack name
- Logical table name (stable identifier grouping all versions/variants)
- Materialization version
- Variant tag (free-form; distinguishes same-version physical variants, e.g. "partitioned by pre_root_id" vs. "partitioned by post_root_id")

**Storage:**
- Storage format (`iceberg` | `delta` | `lance` | `parquet`)
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
| **Delta Lake** | Alternative to Iceberg for mat dumps | Similar capabilities; more Databricks-centric ecosystem. Prefer Iceberg unless Delta is already in use elsewhere in the pipeline. |
| **Lance** | Feature sets requiring ANN similarity search | Native approximate nearest-neighbor index; best for vector similarity workloads on embeddings. |

Partition long-term mat dumps by root ID range.

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
| **Apache Gravitino** | Apache TLP (graduated June 2025, v1.2.0). **Native Iceberg REST catalog** (GCS + credential vending). **Native Lance REST catalog** (first-class format). View support (Iceberg views, v1.2.0). Built-in access control (roles, privileges, OAuth2). Lineage subsystem. Python client. Multi-format (Iceberg, Hudi, Paimon, Lance, JDBC). Docker images. | Java server, heavier than UC. No CAVE semantic roles (requires wrapping). No middle_auth integration. Younger OSS governance (graduated June 2025). | **Strongest option overall.** Native Lance support, working views, and GCS credential vending address three UC gaps. See head-to-head comparison below. |
| **Unity Catalog OSS** | REST API, schema tracking, Iceberg/Delta/UniForm support, credential vending (v0.2+), modular auth planned, DuckDB + Trino integration, lightweight, Helm chart | Views are **not implemented** in OSS (§5 correction). No Lance support. No CAVE semantic roles; no middle_auth integration (requires wrapping); lineage is `❓` on roadmap | Strong for Iceberg-only scope, but Lance and view gaps are significant. |
| **Project Nessie** | Git-like table versioning, Iceberg-native | Iceberg/Delta only; no general metadata model | Good Iceberg catalog backend candidate; poor metadata registry |
| **OpenMetadata** | Custom entity types, connector framework, lineage | Heavy (requires Elasticsearch); enterprise-oriented | Over-engineered for CAVE's scale |
| **DataHub** | Flexible metadata model | Very heavy (Kafka + Elasticsearch) | Far more than needed |
| **Marquez** | Lightweight lineage tracking | Lineage only; no discoverability or schema features | Too narrow |

### Apache Gravitino vs. Unity Catalog — head-to-head

Apache Gravitino (v1.2.0, Apache TLP since June 2025) is a federated metadata catalog that natively supports Iceberg, Lance, Hudi, Paimon, and JDBC databases. The comparison below evaluates it against Unity Catalog OSS (v0.4.0) on every dimension that matters for CAVE.

#### Feature comparison

| Dimension | Unity Catalog OSS | Apache Gravitino | Winner for CAVE |
|-----------|-------------------|------------------|-----------------|
| **Iceberg REST catalog** | Yes (resolves tables to metadata.json) | Yes — full Iceberg REST API spec (Iceberg 1.10.0), JDBC/Hive/REST backends, multi-catalog support, scan plan cache, table metadata cache | Gravitino (more mature) |
| **Lance support** | None | **Native Lance REST catalog** (port 9101) implementing the Lance REST API spec. Register, create, describe, drop tables. Also a **generic lakehouse catalog** (`lakehouse-generic` provider) with first-class Lance table format. | **Gravitino (decisive)** |
| **GCS credential vending** | Yes (since v0.2) | Yes — `GCSTokenCredential` using GCS [Credential Access Boundaries](https://cloud.google.com/iam/docs/downscoping-short-lived-credentials) for scoped temporary tokens. Supports per-table path-scoped credentials. | Tie |
| **View support** | **Not implemented** in OSS (§5 earlier correction: `TableType.MATERIALIZED_VIEW` is marked "not yet fully implemented," no `/views` endpoints, no `ViewService.java`) | **Implemented** in v1.2.0 — Iceberg REST service supports view operations with JDBC backend (schema version V1). Auto-migrates DB schema on first restart. | **Gravitino** |
| **Access control** | Modular auth planned (v0.5 roadmap); limited in v0.4.0 | Built-in roles, privileges, OAuth2. Supports `USE_CATALOG`, `USE_SCHEMA`, `SELECT_TABLE`, etc. Roles can be scoped to catalog, schema, or table. Requires auxiliary mode for full ACL. | Gravitino |
| **Lineage** | `❓` on roadmap | Lineage subsystem present in codebase (`lineage/` directory). On roadmap as implemented. | Gravitino (if functional) |
| **Auth protocols** | OAuth/OIDC in v0.5 roadmap | OAuth2 (since 1.0), Basic auth, HTTPS. Access control for Iceberg REST requires auxiliary mode in Gravitino server. | Gravitino |
| **Python client** | Python SDK | Python Client (` gravitino` package) | Tie |
| **DuckDB integration** | Native (DuckDB reads UC-managed tables) | Via Iceberg REST catalog (DuckDB connects to Gravitino as a standard Iceberg REST endpoint: `http://host:9001/iceberg/`) | Tie (both work) |
| **Spark/Flink/Trino** | Trino connector, Spark connector | Spark, Flink, Trino connectors. Also: Doris, StarRocks, Ray, PyIceberg. | Gravitino (broader) |
| **Deployment weight** | Lightweight Java service, Helm chart | Heavier — Gravitino server (metadata) + Iceberg REST service (aux or standalone) + Lance REST service (aux or standalone). Docker images available. | UC (simpler) |
| **Docker support** | Community images | `apache/gravitino-iceberg-rest:latest`, `apache/gravitino-lance-rest:latest` | Tie |
| **Multi-format metadata** | Iceberg, Delta, UniForm | Iceberg, Delta, Hudi, Paimon, Hive, Lance, JDBC (MySQL/PostgreSQL/Doris/StarRocks/ClickHouse/OceanBase) | Gravitino |
| **Maturity / governance** | Databricks-backed, Linux Foundation | Apache TLP (graduated June 2025), 2.9k stars, 295 contributors, v1.2.0 stable | Both solid; UC has corporate backing, Gravitino has Apache governance |

#### What Gravitino provides that UC doesn't

1. **Native Lance REST catalog.** This is the standout differentiator. Gravitino provides a complete Lance REST service (`/lance/v1/...`) with namespace management, table CRUD, register/deregister. Lance tables stored on GCS work natively. The current CAVE prototype (Phase 5, item 12) planned to handle Lance as a custom adapter outside UC — Gravitino eliminates that entirely.

2. **Working Iceberg view support.** The Gravitino Iceberg REST service implements view operations when using the JDBC catalog backend with `jdbc-schema-version=V1`. This resolves §8.2 (logical views) without custom code.

3. **Lineage.** Present in the codebase; UC has no lineage story.

#### What Gravitino doesn't change

- **CAVE semantic roles, lineage fields, datastack membership** still require a CAVE wrapper service. Gravitino is a metadata catalog, not a domain-specific registry.
- **middle_auth integration** must still be built as a wrapper layer. Gravitino's OAuth2/Basic auth can be proxied through a CAVE Flask service the same way UC's auth would be.
- **Registration validation** (verify mat version exists in ME, validate annotation table references) is CAVE-specific logic independent of the backend.

#### Architecture with Gravitino

```
┌─────────────────┐   ┌──────────────────────┐
│   CAVEclient    │──▶│  CAVE Catalog Service │  (Flask, middle_auth)
│ client.catalog  │   │  CAVE-specific logic: │
└─────────────────┘   │  semantic roles,      │
                      │  lineage, validation  │
                      └──────┬───────────────┘
                             │ proxies to
               ┌─────────────┴──────────────┐
               ▼                            ▼
    ┌──────────────────┐       ┌──────────────────┐
    │  Gravitino       │       │  Gravitino       │
    │  Iceberg REST    │       │  Lance REST      │
    │  (port 9001)     │       │  (port 9101)     │
    └────────┬─────────┘       └────────┬─────────┘
             │                          │
             ▼                          ▼
    ┌──────────────────────────────────────────┐
    │  Gravitino Server (port 8090)            │
    │  Metadata backend, access control,       │
    │  credential vending (GCSTokenCredential) │
    └────────┬─────────────────────────────────┘
             │
             ▼
    ┌──────────────────┐
    │  GCS Catalog     │
    │  Bucket          │
    │  (Iceberg +      │
    │   Lance tables)  │
    └──────────────────┘
```

#### Recommended decision

**Switch from Unity Catalog to Apache Gravitino.** The native Lance REST catalog, working view support, and built-in access control address three of the four gaps identified in UC. The CAVE wrapper complexity is equivalent for either backend (both need middle_auth integration and semantic role metadata). Gravitino adds deployment weight (three services instead of one) but eliminates custom Lance adapter code and the view support gap, for a net reduction in total custom code.

> **[ACTION]** Time-box a Gravitino prototype (same scope as the existing UC prototype): deploy Gravitino via Docker, create an Iceberg catalog on GCS, register a test table, confirm DuckDB can read it via the Iceberg REST endpoint with GCS credential vending. Additionally, test the Lance REST service with a test Lance dataset. If both work, proceed with Gravitino as the backend.

> **[NOTE — Nessie as Iceberg catalog backend]** With Gravitino chosen, Nessie is no longer relevant — Gravitino's Iceberg REST service subsumes the Iceberg catalog backend role.

---

## 6. Boundary with MaterializationEngine

**ME owns the export pipeline; catalog owns the registry.**

- MaterializationEngine writes Iceberg tables to GCS as part of its existing long-term dump workflow (replacing the current CSV export).
- After a successful export, ME calls the catalog registration API to create or update the catalog record for that materialization version.
- The catalog service has no knowledge of export mechanics and never triggers ME.
- The catalog service may query ME to validate that a materialization version exists and is marked "long-term" at registration time (see registration validation, §10 item 3).

**Open storage question:** whether ME's existing `MATERIALIZATION_UPLOAD_BUCKET_PATH` bucket becomes the catalog's managed storage, or a separate bucket is created for catalog-managed assets, still needs to be decided. A separate bucket gives cleaner IAM boundaries (the catalog service's signing key is scoped only to catalog assets) but adds operational overhead.

---

## 7. Migration Path for Existing Data

A new export pipeline will write Iceberg dumps to catalog-managed storage starting from a pre-specified materialization version (chosen at implementation time). Existing gzipped CSV dumps are not migrated or registered. The catalog simply has no records for versions prior to the cutoff — this is acceptable; users needing older data continue to use the existing CSVs directly.

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

**Will the Unity Catalog choice constrain this extension?**

Short answer: **no**. The two components of the non-tabular extension are (a) the registry record and (b) the adapter code, and UC constrains neither:

- **Registry record:** UC has "External volumes" (available since v0.1) — named GCS/S3 URI references with arbitrary key-value properties and credential vending. A Neuroglancer precomputed layer would be registered as a UC external volume with a CAVE property `object_type: neuroglancer_precomputed`. UC doesn't need to understand the format; the URI + CAVE properties are sufficient. Credential vending for volumes works the same way as for tables, so the access control pattern translates directly.
- **Adapter code:** adapters that parse Neuroglancer `info` files or shard indexes are CAVE-side code (in the catalog wrapper service and/or CAVEclient). They are independent of whether the metadata backend is UC or a custom Flask/Postgres service. Choosing UC neither helps nor hinders adapter implementation.
- **UC object hierarchy:** UC organizes objects as catalog → schema → (tables | volumes | functions | models). Non-tabular assets sit in the "volumes" bucket alongside tabular assets in the same UC catalog. This is actually marginally cleaner than a custom service where you'd need to add a new object type to the DB schema.

The one concern that is **not** resolved by the backend choice is the **AnnotationFrameworkInfoService boundary**: AnnotationFrameworkInfoService already registers some of the same sources (image layers, segmentation sources) that a non-tabular extension would catalog. That overlap is a governance question that must be resolved through a boundary agreement with AnnotationFrameworkInfoService independently of what backend the catalog uses.

**Why defer this:**
- The tabular problem is concrete and urgent. Non-tabular assets are a "would be nice."
- The AnnotationFrameworkInfoService boundary needs explicit agreement before any non-tabular registration is built, regardless of backend.
- Each adapter is a maintenance surface (format versioning, error handling for missing/moved objects, read access requirements).

**Recommendation:** build v1 as tabular-only. The UC choice does not paint us into a corner — non-tabular assets can be added in v2 as UC external volumes with CAVE adapter code, with no changes to the backend or the tabular registration path.

### 8.2 Logical views

> **See §5 for the primary treatment of views as a build-vs-buy factor.** Unity Catalog OSS v0.5+ ships native multi-dialect SQL views, Iceberg view support, and materialized views. If Unity Catalog is chosen as the catalog backend, view support is not a future extension — it is available from the start. The options below are only relevant if building a custom service.

A catalog record could represent a **view** — a named SQL query over registered tables, executed locally by DuckDB.

**Options considered:**
- **Option A:** no views; the `enrich()` helper covers the dominant join-by-root-id use case. Users write their own SQL for anything more complex.
- **Option B:** SQL views stored in the catalog, executed locally by DuckDB. Discoverable and shareable. Requires issuing signed URLs for all source tables simultaneously (TTL coordination).
- **Option C (distant future):** server-side query engine (Trino, DuckDB server). Maximum interoperability but significant infrastructure.

For a custom-built catalog, start with Option A; add Option B when needed. The upgrade path A → B → C is clean — SQL text stored in view records can later be handed to a server-side engine. For a Unity Catalog-backed catalog, skip directly to a UC-native view registration workflow.

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

1. **Build vs. buy (Unity Catalog):** Unity Catalog v0.5+ substantially closes the gap with a custom build (credential vending, native views, Iceberg REST catalog, modular auth, DuckDB integration). Recommend a time-boxed prototype wrapping UC before committing to a custom service. See §5.
2. **Iceberg catalog backend:** if building custom — REST catalog (self-hosted), file I/O catalog (GCS path conventions), Nessie, or BigLake Metastore? If using Unity Catalog, this is resolved via UC's Iceberg REST catalog compatibility. Determines deployment topology and operational burden.
2. **ME storage bucket:** use ME's existing `MATERIALIZATION_UPLOAD_BUCKET_PATH` or a dedicated catalog-managed bucket? (See §6.)
3. **Registration validation strictness:** how much should the catalog validate on registration? Spectrum ranges from "store what the registrant provides" to "auto-detect schema, verify mat version exists and is long-term, validate annotation table references." More enforcement = higher catalog quality but more friction. Recommend: validate mat version exists + attempt schema read from data; defer annotation-table-reference validation.

### Important (should resolve before v1 GA)

4. **Feature set versioning misalignment:** when a feature set's computation schedule doesn't align with materialization versions, how is it linked? Options: pin to nearest version, store independently with a timestamp and a `compatible_versions` range, or require feature set producers to always target a specific mat version.
5. **Record immutability policy:** can a `published` record be mutated, or must changes go through deprecate-and-replace? (See §3.2.)
6. **Signed URL TTL and scope:** what expiry, what prefix scope, how to handle URL sharing?

### Deferred (v2 and beyond)

8. **Non-tabular asset scope:** when and how to extend to meshes, precomputed volumes, etc., and what boundary with AnnotationFrameworkInfoService. (See §8.1.)
9. **Logical views:** for a custom-built catalog, when does `enrich()` become insufficient? For a UC-backed catalog, views are available from day one. (See §5 and §8.2.)
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
| **Scope creep into universal asset registry** | High | Delays v1 delivery, complicates AnnotationFrameworkInfoService boundary | Hard scope boundary: v1 = tabular only. Non-tabular is a separate design phase. |
| **Schema overload deters registration** | Medium | Low adoption; catalog becomes empty | Minimal required fields for v1. Optional fields can be backfilled. |
| **Two sources of truth (catalog vs. format)** | Medium | Stale metadata, user confusion | Format-authoritative principle (§2). Catalog caches schema snapshots for display only. |
| **Unity Catalog wrapper complexity underestimated** | Medium | Delayed v1 delivery | Time-box prototype of UC wrapper before committing; see §5. |
| **DuckDB dependency fragments CAVEclient user base** | Medium | Users without DuckDB can't use `open()`/`enrich()` | Optional extras group. Ensure `list()` and `describe()` work without DuckDB. |
| **Signed URL abuse / egress cost** | Low-Medium | Unexpected cloud bills | Access logging, per-user quotas, tiered storage for cold data. |
| **No user validation of `enrich()` pattern** | Medium | Build enrichment API that nobody uses as designed | Validate with 2-3 target users before committing to the `enrich()` interface. |
| **Feature set versioning misalignment** | High | Confusion about which feature set matches which mat version | Resolve design question #4 before implementation. |

---

## 12. Short-Term Implementation Plan

### Decisions fixed for v1

- **Backend:** Unity Catalog OSS as the metadata store (registry, credential vending, views, Iceberg REST catalog).
- **Storage:** a new dedicated GCS bucket, owned and configured by CAVE, used for all catalog-managed assets. Separate from `MATERIALIZATION_UPLOAD_BUCKET_PATH`.
- **Formats:** Iceberg (long-term mat dumps and flat-tabular feature sets), Delta Lake (alternative to Iceberg where already in use in a pipeline), Lance (feature sets requiring ANN).
- **Registration validation:** verify the materialization version exists in ME and is marked long-term; validate any referenced annotation table/column names against CAVE (AnnotationEngine or EMAnnotationSchemas).
- **Cutoff:** catalog starts from a pre-specified materialization version; no backfill of existing CSV dumps.

### Phase 1 — Infrastructure

1. **Provision Unity Catalog instance.** Deploy UC (Helm chart) in the CAVE cluster. Configure authentication to forward to middle_auth (UC v0.5 modular auth). Provision the dedicated catalog GCS bucket and grant UC's service account write access.
2. **Add `terraform-google-cave` resources.** Catalog bucket, UC service, IAM bindings (UC service account → bucket; middle_auth group `catalog:write` → UC registration privileges).
3. **Validate UC credential vending end-to-end.** Confirm that a UC-issued temporary credential scoped to the catalog bucket is usable by DuckDB/PyIceberg to read a test Iceberg table.

### Phase 2 — Materialization export pipeline

4. **Add Iceberg export step to MaterializationEngine.** After a long-term snapshot is marked AVAILABLE, ME writes the merged annotation table(s) as an Iceberg table (parquet-snappy, partitioned by root ID range) to the catalog bucket under a stable path convention: `gs://{catalog_bucket}/{datastack}/{table_name}/mat{version}/`.
5. **Implement catalog registration call in ME.** After a successful export, ME calls UC's table registration API (or CAVE catalog's registration endpoint wrapping UC) with the required record fields: datastack, logical table name, materialization version, storage path, `access_type: managed`, status: `published`.
6. **Implement registration validation.** The registration endpoint verifies: (a) the materialization version exists in ME and is marked long-term; (b) any referenced annotation table/column names resolve against AnnotationEngine or EMAnnotationSchemas.

### Phase 3 — CAVE catalog service (UC wrapper)

7. **New CAVE catalog service.** A thin Flask service that wraps Unity Catalog's REST API. Responsibilities:
   - Translates CAVE-specific concepts (datastacks, semantic roles, lineage fields) into UC metadata fields.
   - Enforces `catalog:write` permission via `middle_auth_client` before proxying registration calls to UC.
   - Enforces datastack read permission before issuing UC credential vending requests.
   - Runs the registration validation logic (step 6).
   - Exposes a stable CAVE-versioned API so clients are insulated from UC API changes.
8. **Helm chart, Docker image, `terraform-google-cave` additions** for the new service.

### Phase 4 — CAVEclient integration

9. **`client.catalog` sub-client.** Implement in CAVEclient as an optional extras group (`pip install caveclient[catalog]`). Core methods for v1:
   - `list(datastack, [table_name], [version], [tags])` — no heavy dependencies required.
   - `describe(datastack, table_name, version)` — no heavy dependencies required.
   - `open(datastack, table_name, version, [variant])` — requires PyIceberg or DuckDB; returns a ready-to-query object backed by UC-issued credentials.
   - `query(datastack, table_name, version, root_ids=...)` — thin filter wrapper over `open()`.
10. **`enrich()` helper.** Validate the join-by-root-id pattern with 2-3 target users before committing to the interface. Implement after `open()` and `query()` are stable.

### Phase 5 — Feature set registration

11. **Documentation and registration guide** for external contributors (data scientists, other labs): required fields, how to request `catalog:write` access, naming conventions for auto-registered tables.
12. **Lance support.** Add Lance dataset registration and UC adapter for ANN-indexed feature sets. Follows the same registration flow as Iceberg but returns a `lance.LanceDataset` from `open()`.
