## 1. Spike: ADBC + PostGIS Validation

- [ ] 1.1 Write a test script that connects to a frozen DB via `adbc-driver-postgresql` and streams a small table with geometry columns; verify Arrow batch types and WKB binary format
- [ ] 1.2 Verify ADBC works through the existing Cloud SQL Auth Proxy connection path used by Celery workers

## 2. Dependencies and Project Setup

- [ ] 2.1 Add `adbc-driver-postgresql`, `deltalake`, and `polars` to MaterializationEngine's `pyproject.toml`
- [ ] 2.2 Verify dependency compatibility with existing packages (especially `pyarrow` version alignment)

## 3. Output Spec and Partition Planning

- [ ] 3.1 Create `materializationengine/workflows/deltalake_export.py` module
- [ ] 3.2 Define `DeltaLakeOutputSpec` dataclass: `partition_by` (str | None), `partition_strategy` ("range" | "hash" | None), `n_partitions` (int | "auto"), `zorder_columns` (list[str]), `bloom_filter_columns` (list[str])
- [ ] 3.3 Implement `discover_default_output_specs(table_name, engine)`: derive default output specs from table metadata (indexes, column types, etc.); exact heuristic TBD but code path must support any column for partition/z-order/bloom
- [ ] 3.4 Implement `resolve_n_partitions(n_partitions, row_count, target_file_size_mb=256)`: if `"auto"`, compute from `MaterializedMetadata.row_count`; otherwise pass through explicit value
- [ ] 3.5 Implement `compute_bucket_boundaries(connection_string, table_name, column_name, n_partitions)`: run `SELECT percentile_disc(generate_series(1, N-1) / N::float) WITHIN GROUP (ORDER BY col) FROM table` to get N-1 boundary values
- [ ] 3.6 Implement `assign_range_bucket(table, column_name, boundaries)`: use `polars.cut()` with percentile boundaries to produce `{column}_partition` column
- [ ] 3.7 Implement `assign_hash_bucket(table, column_name, n_partitions)`: `hash(value) % n_partitions` to produce `{column}_partition` column

## 4. Tests: Output Spec and Partition Planning

- [ ] 4.1 Unit test `discover_default_output_specs` with mock table metadata
- [ ] 4.2 Unit test `resolve_n_partitions` heuristic with various row counts
- [ ] 4.3 Unit test `compute_bucket_boundaries` with a small Postgres table; verify boundary values are approximate percentiles
- [ ] 4.4 Unit test `assign_range_bucket` distribution: verify rows are assigned to buckets with roughly equal counts and contiguous value ranges

## 5. Core Streaming Writer

- [ ] 5.1 Implement `stream_table_to_arrow(connection_string, table_name, chunk_size)`: ADBC streaming reader that yields Arrow RecordBatches. Detect table structure and construct appropriate SQL: JOIN annotation + segmentation tables on `id` (most common), `SELECT *` from already-merged table, or `SELECT *` from annotation-only table (no segmentation columns)
- [ ] 5.3 Implement `decode_geometry_columns(batch, geometry_columns)`: vectorized WKB → `List[Int32]` coordinate array decoding per Arrow batch
- [ ] 5.4 Implement buffered write loop: accumulate batches, on flush → assign buckets per output spec's partition strategy (range, hash, or skip if `None`) → `write_deltalake(..., mode="append", partition_by=...)` for each Delta Lake; when partition strategy is `None`, write without `partition_by`

## 6. Tests: Core Streaming Writer

- [ ] 6.1 Unit test `decode_geometry_columns` with sample WKB binary data
- [ ] 6.2 Integration test: end-to-end export of a small test table to a local Delta Lake (using local Postgres in CI)
- [ ] 6.3 Integration test: verify explicit output specs override index-derived defaults

## 7. Delta Lake Optimization

- [ ] 7.1 Implement `optimize_deltalake(uri, zorder_columns, bloom_filter_columns, fpp)`: z-order, bloom filter creation, and vacuum on a completed Delta Lake
- [ ] 7.2 Read z-order and bloom filter columns from the output spec for each Delta Lake

## 8. Celery Task Wiring

- [ ] 8.1 Create Celery task `write_deltalake_table(datastack_info, version, table_name, output_specs=None)` that orchestrates: resolve specs → compute boundaries → stream → write → optimize
- [ ] 8.2 Add the task module to `celery_init.py` task includes
- [ ] 8.3 Configure a `deltalake` Celery queue in the worker configuration
- [ ] 8.4 Add overwrite logic: detect existing partial Delta Lakes (DeltaTable exists but row count doesn't match MaterializedMetadata) and delete before re-writing

## 9. API Endpoint

- [ ] 9.1 Add `POST /materialize/run/write_deltalake/datastack/{ds}/version/{v}/table_name/{t}/` endpoint on the materialize blueprint
- [ ] 9.2 Accept optional JSON body with `output_specs` list; if absent, use index-derived defaults
- [ ] 9.3 Endpoint enqueues the `write_deltalake_table` Celery task on the `deltalake` queue
- [ ] 9.4 Add admin auth requirement (`auth_requires_dataset_admin`)

## 10. Configuration

- [ ] 10.1 Add config keys to MaterializationEngine app config: `DELTALAKE_OUTPUT_BUCKET`, `DELTALAKE_FLUSH_THRESHOLD_BYTES`, `DELTALAKE_TARGET_PARTITION_SIZE_MB`, `DELTALAKE_CHUNK_SIZE`
- [ ] 10.2 Add GCS bucket provisioning to deployment config / Terraform (or document as a prerequisite)

## Future Work (not part of this change)

- **Bulk workflow**: `write_all_deltalakes` task that fans out `write_deltalake_table` for every table in a frozen database version, chained after `check_tables` in the `create_frozen_database` workflow (gated by config flag)
- **Catalog registration**: After each Delta Lake is written and optimized, register it in the catalog service via `POST /api/v1/assets/register` with format, URI, datastack, mat_version, and output spec details in properties. Include idempotency check to skip re-registration.
