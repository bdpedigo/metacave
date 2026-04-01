# MaterializationEngine — Storage Backend

> Last investigated: 2026-04-01

## Summary

All annotation and materialized data lives in **PostgreSQL/PostGIS**. There is no BigQuery, BigTable, or other OLAP backend — every annotation table, segmentation table, and frozen snapshot is a standard PostgreSQL database. GCS is used for a separate CSV annotation upload feature and (via CloudVolume) for supervoxel lookups at spatial coordinates only.

---

## 1. PostgreSQL Database Tiers

### Live database: `{aligned_volume}`

Holds the "live" (mutable) annotation and segmentation tables shared with AnnotationEngine:

| Table | Contents |
|---|---|
| `{table_name}` | Annotation rows: spatial positions, schema-specific fields, `valid`, `deleted`, `created`, `superceded_id` |
| `{table_name}__{pcg_table_name}` | Segmentation rows: `supervoxel_id`(s), `root_id`(s), `last_updated_ts` — NULL until resolved |
| `annotation_table_metadata` | Per-table metadata: schema type, voxel resolution, description, permissions |
| `SegmentationMetadata` | Per-table segmentation sync state: `last_updated` timestamp for delta-root queries |
| `AnalysisVersion` | Records of each materialization version: version number, timestamp, status, expiry |

The aligned volume name comes from the datastack info service (`AFIS`) at query time; the database is auto-created by `DynamicAnnotationInterface.create_or_select_database()` using `CREATE DATABASE {aligned_volume} TEMPLATE template_postgis` if it does not yet exist.

### Frozen snapshot databases: `{datastack}__mat{N}`

One new PostgreSQL database is created **per materialization run**:

```
CREATE DATABASE {datastack}__mat{N} WITH TEMPLATE {aligned_volume}
```

This is a **PostgreSQL template-copy** (live block-level copy), **not** a `pg_dump`/restore. The source is the `{aligned_volume}` live database, so the snapshot inherits all tables.

After copying, the workflow JOINs the split annotation + segmentation tables into a single flat merged table per annotation type (e.g. `synapse`, `cell_type_local`), drops the unneeded split tables, rebuilds indices, and marks the version `AVAILABLE` in the `AnalysisVersion` table.

The naming function is in `create_frozen_database.py`:
```python
def create_analysis_sql_uri(sql_uri: str, datastack: str, mat_version: int):
    sql_base_uri = sql_uri.rpartition("/")[0]
    analysis_sql_uri = make_url(f"{sql_base_uri}/{datastack}__mat{mat_version}")
    return analysis_sql_uri
```

**Version lifecycle** (config keys in `config.py`):
- `MAX_DATABASES` (default 2): maximum number of live versions retained
- `DAYS_TO_EXPIRE` (default 7): TTL for regular snapshots
- `LTS_DAYS_TO_EXPIRE` (default 30): TTL for long-term-support snapshots
- Celery Beat runs `remove_expired_databases` daily to drop expired databases

---

## 2. How DynamicAnnotationDB Connects

### Entry point

`DynamicAnnotationInterface(url, aligned_volume)` is the public API:

```python
# dynamicannotationdb/interface.py
class DynamicAnnotationInterface:
    def __init__(self, url: str, aligned_volume: str, pool_size=5, max_overflow=5):
        self._sql_url = self.create_or_select_database(url, aligned_volume)
```

It strips the database component from `url` to get the server base URI, then appends `/{aligned_volume}` as the target database name.

### Connection string format

Standard SQLAlchemy PostgreSQL URL:
```
postgresql://user:password@host:port/database
```

Example from dev config and tests:
```
postgres://postgres:materialize@db:5432/materialize      # DevConfig
postgresql://postgres:postgres@localhost:5432/test_volume  # DADB tests
```

### Config key in MaterializationEngine

```python
# materializationengine/config.py
SQLALCHEMY_DATABASE_URI = "postgres://postgres:materialize@db:5432/materialize"  # DevConfig
```

In production this is injected via environment variable or `config.cfg` instance file (loaded by `configure_app()`).

`DatabaseConnectionManager` (in `materializationengine/database.py`) builds per-database engine URIs at runtime by stripping the database segment from `SQLALCHEMY_DATABASE_URI` and re-appending:
```python
sql_base_uri = SQL_URI_CONFIG.rpartition("/")[0]
sql_uri = f"{sql_base_uri}/{database_name}"
```

Pool configuration:
- `DB_CONNECTION_POOL_SIZE` (default 20)
- `DB_CONNECTION_MAX_OVERFLOW` (default 30)
- `pool_recycle=1800`, `pool_pre_ping=True`

---

## 3. Separate Database per Version?

**Yes.** Each materialization run creates a new PostgreSQL database named `{datastack}__mat{N}`. The version number `N` is an incrementing integer stored in the `AnalysisVersion` table of the live `{aligned_volume}` database. Queries to a frozen version go directly to that database via ME's REST API:
```
POST /api/v2/datastack/{ds}/version/{v}/query
```
which opens a session against `{datastack}__mat{v}` directly.

---

## 4. GCS Buckets

Two separate GCS interactions — neither is the primary annotation store:

### A. Imagery / segmentation bucket (read-only, via CloudVolume)

ME uses CloudVolume to look up the supervoxel ID at a spatial coordinate during annotation ingest:
```python
cloudvolume.download_point(coordinate)
```
The bucket URI comes from the datastack's segmentation source info (served by AFIS), not from ME's own config. This bucket is not owned or written to by ME.

### B. CSV annotation upload bucket (`MATERIALIZATION_UPLOAD_BUCKET_PATH`)

ME exposes an upload API (`/materialize/upload/...`) that allows users to submit annotations as CSV files. Those files are staged in a GCS bucket:
- Config key: `MATERIALIZATION_UPLOAD_BUCKET_PATH` (default `"test_annotation_csv_upload"`)
- Client: `google.cloud.storage.Client()` (uses `GOOGLE_APPLICATION_CREDENTIALS`)
- Implementation: `materializationengine/blueprints/upload/storage.py — StorageService`

This is used *before* annotations reach PostgreSQL, not as a persistent store. The CSV is read, annotations are ingested into PostgreSQL, and the bucket entry may be cleaned up.

---

## 5. Redis

Redis is not a data store for annotations but is required infrastructure:

| Use | Config key | Default |
|---|---|---|
| Celery task broker | `CELERY_BROKER_URL` / `REDIS_URL` | `redis://` |
| Celery result backend | `CELERY_RESULT_BACKEND` | same as broker |
| Flask session storage | `SESSION_TYPE = "redis"`, `REDIS_SESSION_DB = 1` | separate DB index |

Dev config: `redis://${REDIS_HOST}:${REDIS_PORT}/0`

---

## 6. Physical Resource Summary

| Resource | Name / address | Purpose |
|---|---|---|
| PostgreSQL/PostGIS (`{aligned_volume}`) | Server from `SQLALCHEMY_DATABASE_URI` | Live annotation + segmentation tables (shared with AE) |
| PostgreSQL (`{datastack}__mat{N}`) | Same server, different DB | Frozen versioned snapshot per materialization run |
| GCS bucket | `MATERIALIZATION_UPLOAD_BUCKET_PATH` | Staging area for CSV annotation uploads |
| GCS imagery/segmentation bucket | Configured in AFIS / CloudVolume source | Supervoxel lookup during ingest (read-only, not owned by ME) |
| Redis | `REDIS_URL` | Celery broker + result backend + Flask session store |

---

## 7. Key File Paths

| File | Contains |
|---|---|
| `materializationengine/config.py` | `SQLALCHEMY_DATABASE_URI`, `REDIS_URL`, `MATERIALIZATION_UPLOAD_BUCKET_PATH`, pool sizes, expiry settings |
| `materializationengine/database.py` | `DatabaseConnectionManager` (per-DB engine factory), `DynamicMaterializationCache` (DADB client cache) |
| `materializationengine/workflows/create_frozen_database.py` | `create_analysis_database` (PostgreSQL template copy), `create_analysis_sql_uri` (name builder) |
| `dynamicannotationdb/interface.py` | `DynamicAnnotationInterface.__init__` and `create_or_select_database` |
| `dynamicannotationdb/database.py` | `DynamicAnnotationDB.__init__` (SQLAlchemy engine creation) |
| `materializationengine/blueprints/upload/storage.py` | `StorageService` (GCS upload bucket client) |
| `materializationengine/dev.env` | Example env vars: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `SQL_HOST`, `REDIS_HOST` |
