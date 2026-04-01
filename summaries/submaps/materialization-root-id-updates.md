# MaterializationEngine — Root ID Updates

> Last investigated: 2026-04-01

## Overview

Root ID resolution lives in two workflows that run on a **live (annotation) database** before a frozen snapshot is created:

1. **Ingest new annotations** — inserts root IDs for annotations that have never been segmented.
2. **Update expired root IDs** — finds roots that have changed since the last update and rewrites them.

Both are orchestrated by `complete_workflow.py` (for frozen-snapshot creation) and `update_database_workflow.py` (for the periodic live-database sync). The order is always: ingest first, then update.

---

## Workflow Entry Points

| Celery task name | File | Purpose |
|---|---|---|
| `orchestration:run_complete_workflow` | `workflows/complete_workflow.py` | Full materialization: ingest → update → freeze |
| `orchestration:update_database_workflow` | `workflows/update_database_workflow.py` | Periodic live-DB sync: ingest → update (no freeze) |
| `orchestration:run_periodic_database_update` | `workflows/update_database_workflow.py` | Scheduler entry point; reads `DATASTACKS` env var |
| `workflow:update_root_ids_task` | `workflows/update_root_ids.py` | Stand-alone expired-root update for one datastack |
| `workflow:process_new_annotations_workflow` | `workflows/ingest_new_annotations.py` | Stand-alone new-annotation ingest |

---

## Path 1 — Ingest New Annotations (INSERT)

**Goal:** populate the segmentation table for annotations that have no segmentation row at all.

```
ingest_new_annotations_workflow(mat_metadata)
  └── for each annotation chunk:
        ingest_new_annotations(chunk, mat_metadata)          [process:ingest_new_annotations]
          ├── get_annotations_with_missing_supervoxel_ids()  # SQL: LEFT JOIN seg table WHERE seg.id IS NULL
          ├── get_cloudvolume_supervoxel_ids()               # CloudVolume.download_point() per coordinate
          ├── get_new_root_ids(supervoxel_data, mat_metadata) # PCG get_roots() per supervoxel
          └── insert_segmentation_data()                     # SegmentationModel.__table__.insert()
```

**PCG call** (`ingest_new_annotations.py` ~L987):
```python
cgclient.get_roots(supervoxel_ids, timestamp=materialization_time_stamp)
```

**DB write** (`ingest_new_annotations.py` ~L1003):
```python
connection.execute(SegmentationModel.__table__.insert(), materialization_data)
```
New rows are inserted; no existing rows are touched.

---

## Path 2 — Update Expired Root IDs (UPDATE)

**Goal:** find root IDs that have been split/merged since `last_updated_ts` and rewrite them with current IDs at `materialization_time_stamp`.

```
update_root_ids_workflow(mat_metadata)
  ├── get_expired_root_ids_from_pcg(mat_metadata)            # PCG get_delta_roots()
  │     └── lookup_expired_root_ids(pcg_table, ts_from, ts_to)
  └── for each expired root chunk:
        update_root_ids(root_id_chunk, mat_metadata)         [workflow:update_root_ids]
          ├── get_supervoxel_id_queries()                    # SQL: SELECT id, root_id, supervoxel_id WHERE root_id IN (expired)
          └── for each supervoxel batch:
                get_new_root_ids(supervoxel_data, mat_metadata) [process:get_new_root_ids]
                  ├── lookup_new_root_ids()                  # PCG get_roots()
                  └── session.bulk_update_mappings()         # UPDATE segmentation table rows
```

### PCG call 1 — find expired roots (`update_root_ids.py` ~L155):
```python
cg_client.get_delta_roots(last_updated_ts, materialization_time_stamp)
# returns (old_roots_array, new_roots_array)
```
`last_updated_ts` defaults to 5 days ago if not set; if `find_all_expired_roots=True`, it is passed as `None` (all roots).

### PCG call 2 — resolve new roots (`update_root_ids.py` ~L342):
```python
cg_client.get_roots(supervoxel_data, timestamp=formatted_mat_ts)
```

### DB write (`update_root_ids.py` ~L320):
```python
session.bulk_update_mappings(SegmentationModel, data)
```
Existing rows are updated in-place; no inserts.

---

## PCG Client Initialization

`chunkedgraph_gateway.py` creates a module-level singleton `chunkedgraph_cache` (a `ChunkedGraphGateway`). The PCG server URL is taken from the environment variable `LOCAL_SERVER_URL` (default: `http://pychunkedgraph-service/`). The underlying client is `caveclient.ChunkedGraphClient`, initialized per PCG table name and cached in a dict.

---

## Database Write Patterns

| Operation | SQLAlchemy call | File & approx. line |
|---|---|---|
| Insert new segmentation rows (ingest) | `SegmentationModel.__table__.insert()` | `ingest_new_annotations.py` ~L1003 |
| Update existing root IDs (update path) | `session.bulk_update_mappings(SegmentationModel, data)` | `update_root_ids.py` ~L320 |
| Backfill missing roots (missing-root path) | `session.bulk_update_mappings(SegmentationModel, data)` | `ingest_new_annotations.py` ~L993 |
| Mark root ID as None (fix bad root) | `session.query(...).filter(...).update({col: None})` | `ingest_new_annotations.py` ~L584 |

After all root IDs are updated, `update_metadata` (`shared_tasks.py` ~L370) writes the current timestamp to the `SegmentationMetadata.last_updated` column, which becomes the `last_updated_ts` window-start for the next run.

---

## Update vs. Create Difference

| | Ingest new annotations | Update expired roots |
|---|---|---|
| Trigger | Annotation has no segmentation row | Root has changed since `last_updated_ts` |
| Supervoxel source | CloudVolume spatial lookup | Re-read from existing segmentation table row |
| PCG call | `get_roots(svids, timestamp)` | `get_delta_roots(ts_from, ts_to)` then `get_roots(svids, timestamp)` |
| DB operation | INSERT | UPDATE (bulk) |
| Materialization time | `datetime.utcnow()` at workflow start | Same `materialization_time_stamp` passed through |

**Full-scan mode**: if `lookup_all_root_ids=True` in `datastack_info`, `get_expired_root_ids_from_pcg` is bypassed entirely and `generate_chunked_model_ids` is used instead — every row in the segmentation table is re-resolved regardless of whether its root has expired. (`update_root_ids.py` ~L79.)

---

## Full Frozen-Snapshot Chain (complete_workflow.py)

```
run_complete_workflow
  ├── create_new_version()                        # insert AnalysisVersion row
  ├── per table:
  │     chain(
  │       ingest_new_annotations_workflow(),      # INSERT new roots
  │       update_root_ids_workflow(),             # UPDATE expired roots
  │     )
  ├── create_materialized_database_workflow()     # pg_dump / copy live DB → versioned DB
  ├── format_materialization_database_workflow()  # merge annotation + segmentation tables
  ├── rebuild_reference_tables()
  ├── check_tables()
  └── set_version_status("AVAILABLE")
```

---

## Gaps / Undocumented Pieces

1. **`get_delta_roots` PCG endpoint** — the actual PCG REST endpoint called by `caveclient.ChunkedGraphClient.get_delta_roots()` is not visible from this repo. The parameters are a `start_timestamp` and `end_timestamp`; the return value is `(old_roots, new_roots)` but the new_roots half is discarded here — only `old_roots` is used to seed the supervoxel re-lookup.

2. **`find_missing_root_ids_workflow` is commented out** — in both `complete_workflow.py` (`~L87`) and `update_database_workflow.py` (`~L95`) the line `# find_missing_root_ids_workflow(mat_metadata), # skip for now` is present but disabled. This means annotations that have a supervoxel ID but no root ID are currently **not** backfilled as part of standard workflows (only the dedicated `process_sparse/dense_missing_roots_workflow` tasks handle them on demand).

3. **Table naming** — the segmentation table name is constructed by `dynamicannotationdb.key_utils.build_segmentation_table_name(annotation_table, pcg_table_name)`; the internals of that function are in `DynamicAnnotationDB`, not here.

4. **Celery queue routing** — `update_root_ids` runs on a `process` queue; throttling via `throttle_celery.wait_if_queue_full(queue_name="process")` is applied when `THROTTLE_QUEUES` config is set. The queue topology is not documented in this repo.
