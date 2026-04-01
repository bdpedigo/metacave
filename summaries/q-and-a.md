## NeuroglancerJsonServer

**Q: What is the backend for NeuroglancerJsonServer?**
The backend is Google Cloud Datastore, accessed via the `DatastoreFlex` library (`datastore-flex` submodule). JSONs are stored as zlib-compressed bytes directly as Datastore entity properties under the `ngl_json` kind, namespaced per deployment (configured via `JSON_DB_TABLE_NAME`); when a column is configured with a `bucket_path`, `DatastoreFlex` transparently redirects that column's bytes to a GCS bucket instead (configured via `NGLSTATE_BUCKET_PATH`).

**Q: What is stored directly in Datastore vs. in GCS for NeuroglancerJsonServer?**
By default, the zlib-compressed JSON bytes are stored as a property directly on the Datastore `ngl_json` entity. GCS is only used for a given column when `DatastoreFlex`'s per-column bucket config is set (via `configure_bucket.py` / `NGLSTATE_BUCKET_PATH`); in that case `DatastoreFlex` reads and writes that column's bytes to GCS and leaves the Datastore entity property absent.

## Dynamic Annotation Orchestration (AE + ME + DADB + EMAS)

**Q: How do MaterializationEngine, AnnotationEngine, DynamicAnnotationDB, and EMAnnotationSchemas work together to orchestrate dynamic annotations?**
EMAnnotationSchemas defines the schema vocabulary (50+ annotation types as marshmallow classes) and generates SQLAlchemy models; DynamicAnnotationDB uses those models to create two parallel PostgreSQL tables per dataset — an annotation table (positions, NULL root IDs initially) and a segmentation table (supervoxel_id + root_id, filled asynchronously). AnnotationEngine accepts user writes into the annotation table and immediately triggers MaterializationEngine via REST to schedule a Celery task that resolves spatial coordinates → supervoxel IDs (via CloudVolume) → root IDs (via PCG `get_roots()`), writing them back into the segmentation table; ME also periodically re-resolves stale root IDs via PCG `get_delta_roots()`. On a scheduled or on-demand trigger, ME runs a full snapshot workflow: `pg_dump` the live database, JOIN annotation + segmentation tables into a flat merged table per type, and mark the frozen `{datastack}__mat{N}` database as AVAILABLE for query. See [submaps/dynamic-annotation-orchestration.md](submaps/dynamic-annotation-orchestration.md) for full mechanics.

## middle_auth

**Q: What packages/services use middle_auth in any way for authentication?**
Every Flask-based CAVE microservice depends on `middle_auth_client` and uses its decorators (`auth_required`, `auth_requires_permission`, `auth_requires_admin`, etc.) to validate tokens against middle_auth at request time; this includes AnnotationEngine, AnnotationFrameworkInfoService, MaterializationEngine, NeuroglancerJsonServer, PyChunkedGraph, PCGL2Cache, SkeletonService, Tourguide, and dash_on_flask. CAVEclient injects the token client-side as a `middle_auth_token` cookie/URL parameter, and EMAnnotationSchemas references the token name in test and Swagger config but does not import `middle_auth_client` directly. middle_auth itself also uses `middle_auth_client` for its own admin UI. See [submaps/middle-auth-usage.md](submaps/middle-auth-usage.md) for the full per-service breakdown.

## PyChunkedGraph (PCG)

**Q: Where and how are meshes stored by the PCG meshing service?**
Meshes are stored in cloud object storage under `{WATERSHED}/{mesh_dir}/`, where `WATERSHED` is a GCS URL (e.g., `gs://bucket/dataset/ws_name`) and `mesh_dir` defaults to `"graphene_meshes"` — both stored per-graph in BigTable as part of `ChunkedGraphMeta`. Initial meshing produces Draco-encoded, Neuroglancer-precomputed sharded (`.shard`) files written under `initial/{layer}/`; post-edit remeshing produces unsharded Draco fragment files under a `dynamic/` subdirectory. All writes use the `CloudFiles` library (not direct GCS or CloudVolume I/O). See [submaps/pcg-meshing-storage.md](submaps/pcg-meshing-storage.md) for full mechanics.

## MaterializationEngine

**Q: How does MaterializationEngine update root IDs in the CAVE stack?**
Root ID resolution follows two distinct paths: (1) new annotations have supervoxel IDs fetched from CloudVolume and then root IDs fetched from PCG via `cg_client.get_roots(supervoxels, timestamp=ts)`, with results **inserted** as new rows into the segmentation table; (2) existing rows are updated by asking PCG for expired roots via `cg_client.get_delta_roots(last_updated_ts, now)`, then re-querying PCG for new root IDs from the same supervoxels, and **bulk-updating** those rows. The complete workflow chains both steps on the live database before copying it to a frozen, versioned snapshot. See [submaps/materialization-root-id-updates.md](submaps/materialization-root-id-updates.md) for full mechanics.

**Q: What is the storage backend for MaterializationEngine? Where is materialized data actually stored?**
All annotation and materialized data is stored in **PostgreSQL/PostGIS** (not BigQuery or any other OLAP system). Two database tiers exist: a live `{aligned_volume}` database (shared with AnnotationEngine) holding split annotation + segmentation tables, and one frozen snapshot database per materialization version named `{datastack}__mat{N}`, created via PostgreSQL's `CREATE DATABASE … WITH TEMPLATE {aligned_volume}` (not pg_dump). A GCS bucket (`MATERIALIZATION_UPLOAD_BUCKET_PATH`) additionally stores CSV annotation uploads; the imagery/segmentation bucket is accessed via CloudVolume only for supervoxel lookups and is not owned by ME. See [submaps/materialization-storage-backend.md](submaps/materialization-storage-backend.md) for full details.
