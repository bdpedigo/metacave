## NeuroglancerJsonServer

**Q: What is the backend for NeuroglancerJsonServer?**
The backend is Google Cloud Datastore, accessed via the `DatastoreFlex` library (`datastore-flex` submodule). JSONs are stored as zlib-compressed bytes directly as Datastore entity properties under the `ngl_json` kind, namespaced per deployment (configured via `JSON_DB_TABLE_NAME`); when a column is configured with a `bucket_path`, `DatastoreFlex` transparently redirects that column's bytes to a GCS bucket instead (configured via `NGLSTATE_BUCKET_PATH`).

**Q: What is stored directly in Datastore vs. in GCS for NeuroglancerJsonServer?**
By default, the zlib-compressed JSON bytes are stored as a property directly on the Datastore `ngl_json` entity. GCS is only used for a given column when `DatastoreFlex`'s per-column bucket config is set (via `configure_bucket.py` / `NGLSTATE_BUCKET_PATH`); in that case `DatastoreFlex` reads and writes that column's bytes to GCS and leaves the Datastore entity property absent.

## Dynamic Annotation Orchestration (AE + ME + DADB + EMAS)

**Q: How do MaterializationEngine, AnnotationEngine, DynamicAnnotationDB, and EMAnnotationSchemas work together to orchestrate dynamic annotations?**
EMAnnotationSchemas defines the schema vocabulary (50+ annotation types as marshmallow classes) and generates SQLAlchemy models; DynamicAnnotationDB uses those models to create two parallel PostgreSQL tables per dataset — an annotation table (positions, NULL root IDs initially) and a segmentation table (supervoxel_id + root_id, filled asynchronously). AnnotationEngine accepts user writes into the annotation table and immediately triggers MaterializationEngine via REST to schedule a Celery task that resolves spatial coordinates → supervoxel IDs (via CloudVolume) → root IDs (via PCG `get_roots()`), writing them back into the segmentation table; ME also periodically re-resolves stale root IDs via PCG `get_delta_roots()`. On a scheduled or on-demand trigger, ME runs a full snapshot workflow: `pg_dump` the live database, JOIN annotation + segmentation tables into a flat merged table per type, and mark the frozen `{datastack}__mat{N}` database as AVAILABLE for query. See [submaps/dynamic-annotation-orchestration.md](submaps/dynamic-annotation-orchestration.md) for full mechanics.

## MaterializationEngine

**Q: How does MaterializationEngine update root IDs in the CAVE stack?**
Root ID resolution follows two distinct paths: (1) new annotations have supervoxel IDs fetched from CloudVolume and then root IDs fetched from PCG via `cg_client.get_roots(supervoxels, timestamp=ts)`, with results **inserted** as new rows into the segmentation table; (2) existing rows are updated by asking PCG for expired roots via `cg_client.get_delta_roots(last_updated_ts, now)`, then re-querying PCG for new root IDs from the same supervoxels, and **bulk-updating** those rows. The complete workflow chains both steps on the live database before copying it to a frozen, versioned snapshot. See [submaps/materialization-root-id-updates.md](submaps/materialization-root-id-updates.md) for full mechanics.
