# Dynamic Annotation Orchestration

> Last investigated: 2026-04-01

## Component Roles

The four components form a layered stack:

| Component | Role |
|---|---|
| **EMAnnotationSchemas** | Schema vocabulary — defines annotation types, field shapes, and SQL column types |
| **DynamicAnnotationDB** | Database ORM — creates and manages the PostgreSQL tables; bridges schemas to storage |
| **AnnotationEngine** | Live annotation CRUD service — accepts user writes, stores positions, triggers segmentation linkage |
| **MaterializationEngine** | Segmentation linker and snapshot engine — resolves root IDs, keeps them current, and freezes versioned tables |

---

## 1. Schema Definition (EMAnnotationSchemas)

EMAnnotationSchemas (EMAS) defines 50+ annotation types (e.g. `synapse`, `cell_type_local`, `nucleus_detection`) as **marshmallow schema classes** registered in a `type_mapping` dict. Each schema is accessed via `get_schema("synapse")`.

The central structural primitive is **`BoundSpatialPoint`** — a nested struct with three sub-fields:
- `position`: 3D voxel coordinate → stored as PostGIS `GEOMETRY(POINTZ)` column
- `supervoxel_id`: integer → stored as `BIGINT`
- `root_id`: integer → stored as `BIGINT`

EMAS provides `make_model_from_schema(table_name, schema_type)` which: (1) fetches the schema class, (2) flattens nested fields (e.g. `pre_pt.position` → `pre_pt_position`), and (3) generates a SQLAlchemy declarative model with corresponding column types. This is what DynamicAnnotationDB calls to create physical tables.

Annotation categories relevant to dynamic annotations:
- **Spatially-referenced** (`BoundSpatialPoint` fields): e.g. `synapse`, `cell_type_local` — these have dynamic root IDs
- **Reference annotations** (`ReferenceAnnotation` subclass): link to another annotation via `target_id` FK; root IDs are inherited from the target

---

## 2. Table Structure (DynamicAnnotationDB)

For every user-created annotation dataset, DADB maintains **two parallel PostgreSQL tables** in the `{aligned_volume}` database:

### Annotation table (`{table_name}`)
Holds spatial data and annotation-specific fields:
- `id` (PK, auto-increment)
- Schema-specific flattened columns: e.g. `pre_pt_position`, `post_pt_position`, `size`
- CRUD housekeeping: `created`, `deleted`, `valid`, `superceded_id`

### Segmentation table (`{table_name}__{pcg_table_name}`)
Holds the segmentation linkage for each annotation row:
- `id` (FK into annotation table)
- Root ID column(s) per `BoundSpatialPoint` field: e.g. `pre_root_id`, `post_root_id`
- Supervoxel ID column(s) per point: e.g. `pre_supervoxel_id`
- `last_updated_ts`

The key design: **supervoxel_id and root_id are NULL at annotation creation time**, and are filled in asynchronously by MaterializationEngine. Multiple segmentation tables can exist for the same annotation table (one per PCG segmentation source).

Updates are **non-in-place**: a `PUT` on an annotation inserts a new row, marks the old row `deleted` and `valid=False`, and sets `superceded_id=new.id` on the old row. This creates an immutable audit chain.

---

## 3. Annotation Ingestion (AnnotationEngine)

When a user `POST`s annotations to AnnotationEngine:

1. **Permission check** via `middle_auth_client` — user must have `edit` on the aligned volume
2. **Insert into annotation table** via `DADB.annotation.insert_annotations()` — only spatial positions and schema-specific fields; root IDs are NULL
3. **Call ME supervoxel lookup** — AE calls MaterializationEngine's `/table/{table}/supervoxel_lookups` endpoint with the list of newly inserted IDs, passing the datastack name and annotation table
4. **Return IDs** to the caller

AnnotationEngine has no knowledge of segmentation; it never queries PyChunkedGraph or CloudVolume directly.

On table creation (`POST /annotation/api/v2/aligned_volume/{av}/table`):
- User specifies `schema_type` (locked at creation, cannot change)
- DADB calls `EMAS.make_model_from_schema()` → creates annotation table in PostgreSQL
- DADB records metadata in `annotation_table_metadata` (description, voxel resolution, permissions, reference table if applicable)

---

## 4. Segmentation Resolution (MaterializationEngine — Live)

When AE calls ME's supervoxel lookup endpoint, ME schedules a **Celery task** (broker: Redis). The task:

1. **Fetch supervoxel IDs** — calls CloudVolume to look up the supervoxel at each annotation's spatial coordinate in the segmentation volume
2. **Resolve to root IDs** — calls PyChunkedGraph `get_roots(supervoxel_ids, timestamp=ts)` where `ts` is the current materialization timestamp
3. **Write back** — updates the segmentation table rows (keyed by annotation `id`) with the resolved supervoxel_id and root_id values via DADB

Periodically (scheduled Celery Beat or on-demand API), ME also **updates expired root IDs**:

1. Calls PCG `get_delta_roots(start_ts=last_updated_ts, end_ts=now)` to find which root IDs changed (due to proofreading edits)
2. For each changed root, extracts the supervoxel IDs from the live segmentation table rows
3. Re-calls PCG `get_roots()` at the new timestamp
4. Bulk-updates those segmentation table rows with new root IDs

Both steps operate on the **live `{aligned_volume}` database** that is shared with AnnotationEngine.

---

## 5. Versioned Snapshot Pipeline (MaterializationEngine — Versioning)

On a scheduled trigger (Celery Beat, e.g. every 2 days) or on-demand via REST, ME runs the full `run_complete_workflow`:

1. **Ingest + update** (same as live track above) — bring live DB fully current
2. **Create `AnalysisVersion` record** — insert row into `AnalysisVersion` table with status `PENDING`, `materialization_time_stamp`, datastack name, version number
3. **`pg_dump` live DB → new database** — copies the live `{aligned_volume}` DB to `{datastack}__mat{version}`
4. **Merge annotation + segmentation tables** — for each table, JOINs annotation and segmentation tables on `id`, filters for `valid=true` and `created <= materialization_time_stamp`, producing a single flat merged table
5. **Format** — drops and rebuilds indices, rebuilds reference annotation join tables
6. **Mark AVAILABLE** — updates `AnalysisVersion.status = AVAILABLE`

Frozen merged table structure (for a `synapse` table):
```
id | pre_pt_position | pre_pt_supervoxel_id | pre_pt_root_id
   | post_pt_position | post_pt_supervoxel_id | post_pt_root_id
   | ctr_pt_position | size | created | valid
```

Old versions are cleaned up when `MAX_DATABASES` (default 2) is exceeded, or after `DAYS_TO_EXPIRE` (default 5) / `LTS_DAYS_TO_EXPIRE` (30).

Users query frozen versions via ME's REST API: `POST /api/v2/datastack/{ds}/version/{v}/query` with a JSON filter DSL, returning up to 200,000 rows in PyArrow or JSON format.

---

## 6. End-to-End Flow Summary

```
User
  │  POST /annotation/.../table  →  AE creates annotation table (schema type locked)
  │  POST /annotation/.../annotations  →  AE inserts rows (root IDs = NULL)
  │                                   → AE calls ME /supervoxel_lookups
  │
MaterializationEngine (Celery)
  │  CloudVolume → supervoxel_id at spatial coords
  │  PCG get_roots() → root_id
  │  DADB writes supervoxel_id + root_id into segmentation table
  │
  │  (periodic) PCG get_delta_roots() → find changed roots
  │             PCG get_roots() → re-resolve → DADB bulk-updates segmentation table
  │
  │  (periodic/on-demand) Full snapshot:
  │    pg_dump live DB → {datastack}__mat{N}
  │    JOIN annotation + segmentation → flat merged table
  │    Mark version AVAILABLE
  │
User
     POST /materialize/.../version/{N}/query  →  ME queries frozen merged table
```

---

## 7. Physical Resources

| Resource | Used by | Purpose |
|---|---|---|
| PostgreSQL/PostGIS (`{aligned_volume}` DB) | AE + ME via DADB | Live annotation + segmentation tables |
| PostgreSQL (`{datastack}__mat{N}` DB) | ME | Frozen versioned snapshot per materialization run |
| Redis | ME | Celery task broker + result backend + Flask session storage |
| GCS/imagery segmentation bucket | ME via CloudVolume | Supervoxel lookup at spatial coordinates |
| PyChunkedGraph (PCG) | ME | Root ID resolution and delta-root queries |
