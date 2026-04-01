# PCG Meshing Storage

> Last investigated: 2026-04-01

## Storage Path Structure

All mesh data lives under the watershed CloudVolume path combined with a mesh subdirectory:

```
{WATERSHED}/{mesh_dir}/
```

- **`WATERSHED`** — a GCS-style URL (e.g., `gs://microns-seunglab/minnie65/ws_minnie65_0`). Stored as `DataSource.WATERSHED` in BigTable per-graph.
- **`mesh_dir`** — defaults to `"graphene_meshes"`. Stored as `custom_data["mesh"]["dir"]` in BigTable per-graph.

Two subdirectory trees exist:

| Subdirectory | Contents | Written by |
|---|---|---|
| `initial/{layer}/` | Sharded `.shard` files for each PCG layer (2, 3, ...) | `chunk_initial_mesh_task()`, `chunk_initial_sharded_stitching_task()` |
| `{dynamic_mesh_dir}/` | Unsharded flat fragment files for post-edit remesh | `chunk_initial_mesh_task()` (sharded=False), `chunk_stitch_remeshing_task()` |

`dynamic_mesh_dir` defaults to `"dynamic"` (`custom_data["mesh"].get("dynamic_mesh_dir", "dynamic")`).

**Example concrete paths (from test helper):**
```
gs://microns-seunglab/minnie65/ws_minnie65_0/graphene_meshes/initial/2/<shard>.shard
gs://microns-seunglab/minnie65/ws_minnie65_0/graphene_meshes/dynamic/<node_id>:0:<bbox>
```

## Format

- **Encoding:** Draco (Google's mesh compression library), encoded via `DracoPy.encode_mesh_to_buffer()`.
- **Initial meshing output:** Neuroglancer precomputed **sharded** format — each output file is a `.shard` binary produced by `ShardingSpecification.synthesize_shard(merged_meshes)`. Shard filenames are determined by `cv.mesh.readers[layer].get_filename(chunk_id)`.
- **Dynamic remesh output:** Neuroglancer precomputed **unsharded** format — one file per fragment, named `{node_id}:0:{chunk_bbox_str}`.
- A global Draco quantization grid ensures chunk-boundary vertices snap to the same grid point when meshed from either adjacent chunk (see `docs/meshing.md` / `pychunkedgraph/meshing/README.md` for the math).

## Write Library

Actual writes use **`CloudFiles`** (from the `cloud-files` package, imported as `from cloudfiles import CloudFiles`):

```python
cf = CloudFiles(mesh_dst)
cf.put(path, content, compress=False, cache_control="public")
```

`cloudvolume.CloudVolume` is used only to: (a) resolve the GCS cloud path (`cv.cloudpath`), (b) read mesh sharding metadata/specs, and (c) fetch fragments for stitching — it does **not** perform the write itself. There is no direct GCS client usage.

## Key Functions

| Function | File | Role |
|---|---|---|
| `chunk_initial_mesh_task()` | `pychunkedgraph/meshing/meshgen.py` | Layer-2 meshing: downloads segmentation via CloudVolume, runs zmesh (marching cubes + simplification), encodes with Draco, writes shard to `initial/2/` |
| `chunk_initial_sharded_stitching_task()` | `pychunkedgraph/meshing/meshgen.py` | Layer 3+ stitching: reads layer-2 shards via CloudFiles, merges Draco fragments across chunk boundaries, writes output shard to `initial/{layer}/` |
| `chunk_stitch_remeshing_task()` | `pychunkedgraph/meshing/meshgen.py` | Post-edit stitching: reads children fragments via CloudFiles from unsharded path, merges, writes to `dynamic/` unsharded path |
| `remeshing()` | `pychunkedgraph/meshing/meshgen.py` | Orchestrates a full post-edit remesh: calls `chunk_initial_mesh_task()` for affected L2 chunks, then walks the hierarchy calling `chunk_stitch_remeshing_task()` for each layer up to `stop_layer` |
| `callback()` | `workers/mesh_worker.py` | Pub/Sub entry point: deserializes message, extracts `l2ids` and config from BigTable, constructs `mesh_path`, calls `meshgen.remeshing()` |
| `_remeshing()` / `remeshing()` | `pychunkedgraph/app/meshing/common.py`, `tasks.py` | HTTP-triggered entry points: same logic as `callback()`, called from Flask routes |

## Configuration Keys

All mesh configuration is stored in BigTable per-graph as part of `ChunkedGraphMeta`. The `custom_data["mesh"]` dict contains:

| Key | Source | Default | Purpose |
|---|---|---|---|
| `dir` | `custom_data["mesh"]["dir"]` | `"graphene_meshes"` | Subdirectory under WATERSHED for all mesh data |
| `dynamic_mesh_dir` | `custom_data["mesh"].get("dynamic_mesh_dir", "dynamic")` | `"dynamic"` | Subdirectory under `dir` for dynamic/remesh fragments |
| `max_layer` | `custom_data["mesh"]["max_layer"]` | (required) | PCG layer up to which remeshing propagates |
| `mip` | `custom_data["mesh"]["mip"]` | (required) | MIP level used for segmentation download |
| `max_error` | `custom_data["mesh"]["max_error"]` | (required) | Max error for zmesh quadratic simplification |
| `verify` | `custom_data["mesh"].get("verify", False)` | `False` | Whether to verify sharded mesh |

`DataSource.WATERSHED` is the GCS CloudVolume path (also stored in BigTable per-graph).

**Environment variable:** `PYCHUNKEDGRAPH_REMESH_QUEUE` — the messaging queue name read by `workers/mesh_worker.py` at startup to subscribe to post-edit remesh events.

## What Triggers Meshing

**Initial (scan-time) meshing** — two paths:
1. **SQS task queue** via `pychunkedgraph/meshing/meshing_sqs.py`: `MeshTask` objects are enqueued per-chunk; `pychunkedgraph/meshing/mesh_worker.py` polls the queue and calls `meshgen.chunk_initial_mesh_task()` (layer 2) or `meshgen.chunk_initial_sharded_stitching_task()` (layer 3+).
2. **Batch script** via `pychunkedgraph/meshing/meshing_batch.py`: iterates over chunk coordinate ranges and calls the same functions directly.

**Post-edit remeshing** — triggered by PCG graph operations:
- After every edit, PCG publishes a Pub/Sub message containing the affected level-2 node IDs and operation ID.
- `workers/mesh_worker.py` subscribes to the queue (named by `PYCHUNKEDGRAPH_REMESH_QUEUE`) via `MessagingClient`, reads mesh config from BigTable, and calls `meshgen.remeshing()`.
- The API can also trigger remesh directly via Flask routes in `pychunkedgraph/app/meshing/`.

## Chunking Strategy

- Meshing starts at **layer 2** (finest granularity) — each chunk is independently meshed.
- A 1-voxel overlap in +x/+y/+z is downloaded with each chunk so that adjacent chunks share mesh vertices at their boundaries.
- Higher layers (3+) **stitch** rather than re-mesh from voxels: they load the pre-existing child fragments and merge them using Draco decoding → boundary transform → re-encoding.
- The Draco quantization grid origin and size are globally consistent across chunks at each layer to guarantee identical boundary vertex positions when meshed from either side (see `get_draco_encoding_settings_for_chunk()` in `meshgen.py`).
