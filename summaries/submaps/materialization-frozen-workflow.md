# MaterializationEngine — Frozen Database Workflow

> Last investigated: 2026-04-16

## Overview

The frozen database workflow creates a time-locked snapshot of all annotations with resolved segmentation IDs. The entry point is `create_versioned_materialization_workflow` in `materializationengine/workflows/create_frozen_database.py`.

## Complete Task Chain

```
create_versioned_materialization_workflow(datastack_info, days_to_expire, merge_tables)
│
├── create_materialized_database_workflow = chain(
│       create_analysis_database          # CREATE DATABASE {datastack}__mat{N} WITH TEMPLATE {aligned_volume}
│       create_materialized_metadata      # Create MaterializedMetadata table with per-table row counts
│       update_table_metadata             # Insert AnalysisTable rows in live DB for tracking
│       drop_tables                       # Drop copied metadata/system tables not needed in frozen DB
│   )
│
├── IF merge_tables (default=True):
│       format_workflow = [               # One chain per non-reference table, run as chord (parallel)
│           chain(merge_tables, add_indices),
│           chain(merge_tables, add_indices),
│           ...
│       ]
│       analysis_database_workflow = chain(
│           chord(format_workflow, fin),   # Wait for all parallel merge+index chains
│           rebuild_reference_tables       # Then merge+index reference tables (need FKs to exist)
│       )
│   ELSE:
│       analysis_database_workflow = fin
│
└── check_tables(mat_info, new_version_number)  # Validate row counts + index consistency
```

### Why reference tables are deferred

Reference tables have foreign keys pointing to other annotation tables. They must be built after the tables they reference are fully merged and indexed, hence the chord → rebuild_reference_tables ordering.

## Database Creation

`create_analysis_database` creates the frozen database via PostgreSQL's template-copy mechanism:

```sql
CREATE DATABASE {datastack}__mat{N} WITH TEMPLATE {aligned_volume}
```

Before copying, it terminates all connections to the live database (`pg_terminate_backend`). This is a block-level copy, not a logical dump/restore.

## Table Merge (merge_tables task)

The merge JOINs the split annotation table and segmentation table into one flat table:

```sql
CREATE TABLE temp__{annotation_table} AS (
    SELECT {sorted_columns}
    FROM {annotation_table}
    JOIN "{segmentation_table}"
        ON {annotation_table}.id = "{segmentation_table}".id
    WHERE {annotation_table}.id = "{segmentation_table}".id
    AND {annotation_table}.created <= '{mat_time_stamp}'
    AND {annotation_table}.valid = true
);
DROP TABLE {annotation_table}, "{segmentation_table}" CASCADE;
ALTER TABLE temp__{annotation_table} RENAME TO {annotation_table};
```

The merged table has columns from both sides (spatial positions + schema fields + supervoxel_ids + root_ids) but **excludes** CRUD columns (`created`, `deleted`, `superceded_id`). Column order is determined by `create_table_dict()` from the flattened schema.

For reference tables that have no segmentation table (`segmentation_table_name` is None), only `add_indices` runs (no merge).

## Index Creation

### Where indexes are defined

Indexes are **not externally configured**. They are derived from EMAnnotationSchemas marshmallow field metadata. In `emannotationschemas/models.py:add_column()`:

```python
has_index = field.metadata.get("index", False)
# Column(..., index=has_index)
```

### Which fields have index=True

From `emannotationschemas/schemas/base.py`:

| Field | Schema class | Index type |
|---|---|---|
| `position` (PostGISField) | `SpatialPoint` | GIST spatial (`gist_geometry_ops_nd`) |
| `root_id` (SegmentationField) | `BoundSpatialPoint` | B-tree |
| `target_id` (ReferenceTableField) | `ReferenceAnnotation` | B-tree + FK constraint |
| `created`, `deleted` | CRUD columns | B-tree (only when `with_crud_columns=True`, dropped in frozen) |

`supervoxel_id` does **not** have `index=True`.

### Concrete example: synapse table

A synapse has `pre_pt` and `post_pt` (each `BoundSpatialPoint`). After flattening (`pre_pt_position`, `post_pt_position`, `pre_pt_root_id`, `post_pt_root_id`, etc.), the frozen table gets:

- `id` — primary key
- `pre_pt_position` — GIST spatial index
- `post_pt_position` — GIST spatial index
- `pre_pt_root_id` — B-tree index
- `post_pt_root_id` — B-tree index

### How indexes are applied (add_indices task)

1. Build a flat SQLAlchemy model from the schema via `make_flat_model()` (or `make_reference_annotation_model()` for reference tables)
2. Call `index_cache.add_indices_sql_commands(table_name, model, engine)` which:
   - Reflects current table indexes via SQLAlchemy inspector (`get_table_indices`)
   - Computes expected indexes from the model (`get_index_from_model`)
   - Returns SQL commands for the **difference** (missing indexes only)
3. Each SQL command becomes a separate Celery task (`add_index`) chained sequentially

Generated SQL patterns (`index_manager.py:add_indices_sql_commands`):

```sql
-- B-tree index
CREATE INDEX IF NOT EXISTS ix_{table}_{column} ON {table} ({column});

-- GIST spatial index
CREATE INDEX IF NOT EXISTS idx_{table}_{column} ON {table} USING GIST ({column} gist_geometry_ops_nd);

-- Primary key
ALTER TABLE {table} add primary key({column});

-- Foreign key
ALTER TABLE "{table}" ADD CONSTRAINT {fk_name} FOREIGN KEY ("{column}") REFERENCES "{ref_table}" ("{ref_column}");
```

Each index creation boosts `maintenance_work_mem` to 1GB for speed:

```sql
SET maintenance_work_mem to '1GB';
{index_command}
SET maintenance_work_mem to '64MB';
```

## Validation (check_tables task)

The final step compares each table's row count in the live database (filtered by `valid=True` and `created <= mat_timestamp`) against the frozen database. It also compares the model-expected indexes against the actual reflected indexes, logging warnings on mismatch. Each table's `AnalysisTable.valid` is set to `True` if counts match.

## Key Code Locations

| Component | File |
|---|---|
| Workflow chain | `materializationengine/workflows/create_frozen_database.py` |
| Index cache / SQL generation | `materializationengine/index_manager.py` |
| `add_index` Celery task | `materializationengine/shared_tasks.py` |
| Schema field index metadata | `emannotationschemas/schemas/base.py` |
| Model generation from schema | `emannotationschemas/models.py` |
| `make_flat_model` | `emannotationschemas/models.py` |
