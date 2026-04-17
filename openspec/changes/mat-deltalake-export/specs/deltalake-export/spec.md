## ADDED Requirements

### Requirement: Stream table to Delta Lake via ADBC
The system SHALL stream a materialized table from a frozen PostgreSQL database to one or more Delta Lake files on cloud storage using ADBC (Arrow Database Connectivity), without producing intermediate CSV files.

#### Scenario: Successful export of a merged table
- **WHEN** the delta lake export task is invoked for a datastack, version, and table name that exists in the frozen database as a merged flat table
- **THEN** the system SHALL connect to the frozen database via ADBC, stream the full table as Arrow RecordBatches, and write one or more Delta Lake files to the configured cloud storage bucket

#### Scenario: Table with unmerged annotation and segmentation tables
- **WHEN** the target table exists as separate annotation and segmentation tables in the frozen database (not yet merged)
- **THEN** the system SHALL join the annotation and segmentation tables on `id` in the SQL query before streaming, producing the same flat schema as a merged table

### Requirement: Configurable Delta Lake output specs
The system SHALL accept a list of output specs, where each spec defines `partition_by` (column name or null), `partition_strategy` (e.g., `"range"` or `"hash"`; defaults to `"range"`), `n_partitions` (integer or `"auto"`), `zorder_columns` (list of column names), and `bloom_filter_columns` (list of column names). Each output spec produces one Delta Lake.

#### Scenario: Explicit output specs provided
- **WHEN** the export task is invoked with explicit output specs (e.g., via the API endpoint)
- **THEN** the system SHALL produce one Delta Lake per output spec, using the specified partition column, z-order columns, and bloom filter columns

#### Scenario: No output specs provided (default derivation)
- **WHEN** the export task is invoked without explicit output specs
- **THEN** the system SHALL derive a reasonable set of default output specs from table metadata (indexes, column types, etc.). The exact default derivation strategy is TBD.

#### Scenario: Output spec with no partition column
- **WHEN** an output spec has `partition_by: null`
- **THEN** the system SHALL produce a flat (non-partitioned) Delta Lake, z-ordered on the specified columns

### Requirement: Configurable partition assignment strategy
When a partition column is specified, the system SHALL support multiple partition assignment strategies, configurable per output spec. At minimum, the system SHALL implement:

#### Scenario: Range bucketing
- **WHEN** an output spec specifies range bucketing as the partition strategy
- **THEN** the system SHALL query Postgres for approximate percentile boundary values and assign each row to a bucket by binary search, preserving natural ordering so that Parquet min/max statistics per bucket are contiguous

#### Scenario: Hash bucketing
- **WHEN** an output spec specifies hash bucketing as the partition strategy
- **THEN** the system SHALL assign rows to buckets via `hash(value) % n_partitions`

### Requirement: Row-count-based partition count heuristic
When `n_partitions` is set to `"auto"`, the system SHALL determine the number of partitions using a heuristic based on the table's row count and a configurable target partition file size.

#### Scenario: Large table partition count
- **WHEN** exporting a table with 500 million rows, `n_partitions: "auto"`, and the target partition file size is 256 MB
- **THEN** the system SHALL compute a partition count that targets approximately 256 MB per partition file after columnar compression

#### Scenario: Small table gets minimal partitions
- **WHEN** exporting a table with 50,000 rows and `n_partitions: "auto"`
- **THEN** the system SHALL use a partition count of 1 (no partitioning)

#### Scenario: Explicit partition count
- **WHEN** an output spec specifies `n_partitions: 64`
- **THEN** the system SHALL use exactly 64 partitions, regardless of table size

### Requirement: Bounded memory via chunked streaming
The system SHALL stream data in chunks and flush to Delta Lake when a configurable byte-size threshold is exceeded, keeping peak memory usage bounded regardless of total table size.

#### Scenario: Table larger than flush threshold
- **WHEN** streaming a 50 GB table with a flush threshold of 2 GB
- **THEN** the system SHALL flush accumulated Arrow batches to all target Delta Lakes each time the buffer exceeds 2 GB, rather than accumulating the full table in memory

### Requirement: Delta Lake optimization
The system SHALL optimize each written Delta Lake with z-ordering and bloom filters as specified in its output spec.

#### Scenario: Z-ordering applied per output spec
- **WHEN** a Delta Lake with `zorder_columns: ["post_pt_root_id", "id"]` is fully written
- **THEN** the system SHALL apply z-ordering on `post_pt_root_id` and `id`

#### Scenario: Bloom filters applied per output spec
- **WHEN** a Delta Lake with `bloom_filter_columns: ["id"]` is fully written
- **THEN** the system SHALL apply bloom filters on the `id` column

#### Scenario: No z-order columns specified
- **WHEN** an output spec has `zorder_columns: []`
- **THEN** the system SHALL skip z-ordering for that Delta Lake

### Requirement: Ad-hoc API trigger
The system SHALL expose an API endpoint to trigger delta lake export for a specific datastack, version, and table.

#### Scenario: Trigger export for a specific table
- **WHEN** an authenticated admin sends `POST /materialize/run/write_deltalake/datastack/{ds}/version/{v}/table_name/{t}/`
- **THEN** the system SHALL enqueue a Celery task to export that table as Delta Lake(s) using default output specs derived from indexes

#### Scenario: Trigger export with explicit output specs
- **WHEN** an authenticated admin sends the same endpoint with a JSON body containing an `output_specs` list
- **THEN** the system SHALL use the provided output specs instead of deriving defaults from indexes

### Requirement: Geometry column decoding
The system SHALL decode PostGIS geometry (WKB binary) columns into coordinate arrays before writing to Delta Lake.

#### Scenario: Point geometry column
- **WHEN** a table contains a POINTZ geometry column (e.g., `pt_position`)
- **THEN** the system SHALL decode the WKB binary into a list of 3 integer coordinates `[x, y, z]` stored as `List[Int32]` in the Delta Lake
