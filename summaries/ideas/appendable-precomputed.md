# Idea: Appendable Neuroglancer Precomputed Annotations via Sub-Tree Merging

## Background: The Neuroglancer Precomputed Annotation Format

Neuroglancer can load annotations (points, lines, bounding boxes, ellipsoids) from any HTTP server that implements the "precomputed" annotation format. The server exposes a tree of files rooted at a base URL:

- **`info`** — a JSON file declaring the annotation type, coordinate space, typed per-annotation properties (e.g. `confidence: uint8`, `label: rgba`), and the structure of the spatial index.
- **`by_id/{annotation_id}`** — binary record for a single annotation fetched by its uint64 ID.
- **`spatial/{level}/{gx}_{gy}_{gz}`** — binary chunk containing all annotations belonging to grid cell `(gx, gy, gz)` at hierarchy level `level`. Neuroglancer fetches these progressively as the user navigates.

Binary records are packed little-endian: float32 geometry, typed property values, uint64 IDs.

### The Spatial Hierarchy

The spatial index is a coarse-to-fine octree. The `info` JSON defines multiple levels, each with a `grid_shape` (e.g. `[1,1,1]` at level 0, `[2,2,2]` at level 1, `[4,4,4]` at level 2, ...) and a `limit` (max annotations per chunk). Neuroglancer loads coarse levels first for an overview, then requests finer cells as the user zooms in. Each annotation appears at **exactly one level** — the coarsest level where its containing cell has not yet reached `limit` — so annotations are not rendered twice.

### Writing Precomputed Annotations Today

cloud-volume's `PrecomputedAnnotationSource` is the standard Python implementation for **reading** precomputed annotation trees. It supports `get_by_bbox`, `get_by_id`, `get_all`, and `get_by_relationship` queries. However, **cloud-volume has no annotation writer**. The write side was planned (constructor accepts a `readonly` parameter, and a `tobytes()` stub exists) but never implemented. There is no method to serialize annotations to binary, build spatial indices, or upload precomputed annotation trees.

In practice, precomputed annotation files are produced by custom scripts in the MaterializationEngine pipeline, not by a reusable cloud-volume API. Writing a precomputed annotation tree requires implementing: binary encoding of annotation records, the capacity-based level-assignment algorithm, spatial chunk file layout, and the by-ID index.

This means any proposal involving precomputed annotation writes — including this one — requires building a writer as a prerequisite.

## The Problem

CAVE annotation data is served to Neuroglancer from precomputed files on GCS, produced by the MaterializationEngine as part of periodic materialization runs:

```
PostgreSQL (live annotations) → MaterializationEngine → cloud-volume write → GCS precomputed files → Neuroglancer
```

This pipeline has two limitations:

1. **No incremental updates.** Adding even one annotation requires rewriting the full precomputed tree. For tables with millions of annotations, this is slow and wasteful.
2. **No concurrent writers.** The all-or-nothing write model means only one process can produce the precomputed output. Distributed systems that independently generate annotations (e.g. automated detection pipelines running across spatial chunks) cannot write to the same precomputed layer concurrently.
3. **No memory-bounded writes.** The capacity-based level-assignment algorithm requires all annotations in memory to compute cell counts and overflow. For large datasets, this is a bottleneck even if you could tolerate rewriting the full tree.

The goal is to make the precomputed format appendable — support incremental, concurrent, and memory-bounded writes — while keeping the existing format, tooling, and serving path intact.

## Key Insight: A Precomputed Layer as an Ordered List of Sub-Trees

A single precomputed annotation tree is a valid, self-contained unit: it has its own `info`, `spatial/` chunks, and `by_id/` index. Nothing in the Neuroglancer binary protocol requires that a layer be served from exactly one such tree.

The core idea is: **represent a logical annotation layer as an ordered list of one or more precomputed sub-trees, merged at read time by a thin HTTP adapter.** Each sub-tree is independently written using standard cloud-volume tooling. The adapter merges responses from all sub-trees transparently — Neuroglancer sees a single layer.

This is essentially an **LSM-tree** (Log-Structured Merge Tree) applied to the precomputed annotation format:

| LSM concept | Precomputed equivalent |
|---|---|
| Sorted run | Sub-tree (a complete precomputed annotation tree) |
| Write | Produce a new sub-tree and add it to the manifest |
| Read | Fan out across all sub-trees, merge results |
| Compaction | Merge N sub-trees into fewer (or one), rewrite via cloud-volume |

Sub-trees can be scoped in any way:

- **Temporally** — each append batch produces a new sub-tree (incremental updates)
- **Spatially** — each sub-tree covers a region of the volume (distributed writers, memory-bounded writes)
- **Both** — a distributed pipeline writes per-region sub-trees, appended over time

The adapter doesn't distinguish between these — it just iterates the manifest and merges.

### The Manifest

A JSON document listing the ordered set of active sub-trees:

```json
{
  "sub_trees": [
    {"path": "trees/0001/", "created": "2026-03-01T00:00:00Z"},
    {"path": "trees/0002/", "created": "2026-03-15T00:00:00Z"},
    {"path": "trees/0003/", "created": "2026-04-01T00:00:00Z"}
  ],
  "version": 42
}
```

Ordering matters for conflict resolution: later sub-trees override earlier ones for the same annotation ID (latest writer wins). The manifest is the only coordination point between writers.

### Storage Layout

```
gs://bucket/datastack/annotations/synapses/
├── trees/
│   ├── 0001/                      # Sub-tree (e.g. initial bulk load)
│   │   ├── info
│   │   ├── spatial/0/0_0_0
│   │   ├── spatial/1/...
│   │   └── by_id/...
│   ├── 0002/                      # Sub-tree (e.g. append batch)
│   │   ├── info
│   │   ├── spatial/...
│   │   └── by_id/...
│   └── 0003/                      # Sub-tree (e.g. another append or spatial region)
│       ├── info
│       ├── spatial/...
│       └── by_id/...
└── manifest.json
```

Each sub-tree under `trees/` is a standard, complete precomputed annotation tree — readable by cloud-volume directly, writable with the existing `PrecomputedAnnotationSource.upload()` code. No new serialization format.

## Write Path

### Appending New Annotations

A writer wants to add N new annotations:

1. Assign unique annotation IDs.
2. Build a precomputed annotation tree over just the new annotations, using a precomputed annotation writer (see below).
3. Write it to `trees/{next_seq}/` on GCS.
4. Atomically update `manifest.json` to append the new sub-tree.

Multiple writers append concurrently — each writes to a different sub-tree path. The only coordination is the manifest update (a single small JSON object; GCS supports atomic object writes).

**Writer prerequisite:** cloud-volume currently has no annotation writer (see [Writing Precomputed Annotations Today](#writing-precomputed-annotations-today)). A precomputed annotation writer must be built first. This writer needs to: encode annotation records to the precomputed binary format, run the capacity-based level-assignment algorithm, write spatial chunk files, and write the by-ID index. The writer is the same regardless of whether annotations are served from a single tree or from merged sub-trees — this proposal does not change the per-tree write logic, only how trees are composed. The writer could live in cloud-volume (completing the planned but unimplemented write path) or as a standalone library.

### Memory-Bounded Initial Load

For a large initial dataset that doesn't fit in memory, **spatially partition** the annotations and write each partition as its own sub-tree:

1. Divide the volume into coarse spatial regions (e.g. level-2 or level-3 grid cells).
2. Stream annotations, routing each to the appropriate region.
3. For each region, build and upload a sub-tree containing only annotations in that region (using the precomputed annotation writer).
4. Write a manifest listing all region sub-trees.

Each sub-tree is a fraction of the full dataset. Regions are independent and can be written **in parallel** across workers. Memory per worker is bounded by the size of one region.

**Caveat:** With spatial partitioning, the coarsest levels (0, 1) span multiple regions. Each sub-tree's level-0 cell contains only its region's annotations, not the full volume's. This means coarse-level chunks will be under-populated per sub-tree — but the adapter merges across sub-trees, so the merged coarse-level response contains annotations from all regions. The progressive-loading `limit` semantics across the merged view are approximate (see [Progressive Loading and the `limit` Problem](#progressive-loading-and-the-limit-problem) below). Compaction resolves this.

### Distributed / Concurrent Writes

Multiple independent processes (e.g. detection pipeline workers, each covering a spatial chunk) each write their own sub-tree to a unique path and append to the manifest. No inter-writer coordination beyond the manifest.

## Read Path (HTTP Adapter)

A thin FastAPI service translates Neuroglancer requests into fan-out reads across all sub-trees, then merges.

**Spatial chunk request** (`GET /spatial/{level}/{gx}_{gy}_{gz}`):

```python
async def get_spatial_chunk(level: int, gx: int, gy: int, gz: int):
    manifest = get_cached_manifest()
    chunk_key = f"spatial/{level}/{gx}_{gy}_{gz}"

    # Fan out reads to all sub-trees in parallel
    raw_chunks = await asyncio.gather(*[
        fetch_if_exists(f"{st['path']}/{chunk_key}")
        for st in manifest["sub_trees"]
    ])

    # Decode, merge, re-encode
    all_records = []
    all_ids = []
    for raw in raw_chunks:
        if raw is not None:
            records, ids = decode_spatial_chunk(raw)
            all_records.extend(records)
            all_ids.extend(ids)

    return encode_spatial_chunk(all_records, all_ids)
```

**By-ID request** (`GET /by_id/{annotation_id}`):

```python
async def get_by_id(annotation_id: int):
    manifest = get_cached_manifest()
    # Check sub-trees in reverse order (newest first)
    for st in reversed(manifest["sub_trees"]):
        result = await fetch_if_exists(f"{st['path']}/by_id/{annotation_id}")
        if result is not None:
            return result
    return 404
```

The reverse-order scan for by-ID naturally supports **updates and deletes**: a newer sub-tree can contain an updated version of an existing annotation (latest writer wins). Deletes can be represented as tombstone markers.

## Progressive Loading and the `limit` Problem

Within each sub-tree, cloud-volume's capacity-based level-assignment algorithm runs independently. Each sub-tree individually has correct progressive loading semantics — coarse levels capped at `limit` per cell, overflow to finer levels.

Merging across sub-trees at read time can violate the `limit` invariant. The merged response for a coarse cell could exceed `limit` (sum of contributions from each sub-tree at that level).

Three options, in order of complexity:

**Option A: Accept the overshoot.** When sub-trees are numerous but individually small relative to the total, the overshoot per cell is modest. Neuroglancer renders all returned annotations without enforcing a limit client-side. Visually negligible for typical append workloads. Compaction resolves it periodically.

**Option B: Adapter-side cap.** After merging, if a chunk exceeds `limit`, truncate to `limit` annotations (preferring earlier/larger sub-trees for stability). Annotations dropped from a coarse level still appear at a finer level within their sub-tree. Minor cross-level duplication is possible but negligible when sub-tree count is bounded.

**Option C: Overlay-aware sub-tree construction.** When building a new sub-tree, read existing sub-trees' per-cell annotation counts (metadata only, not data). Assign new annotations to levels using existing counts as starting capacity. This produces a sub-tree that complements all prior sub-trees — no overlap, correct limits. Cost: the writer must read metadata from all existing sub-trees. This is the correct approach if pixel-perfect progressive loading is required between compactions.

Compaction (below) eliminates all of these concerns, restoring exact level-assignment semantics.

## Compaction

Compaction merges N sub-trees into fewer (typically one), producing a fresh precomputed tree with correct capacity-based level assignment. This is the direct analog of LSM compaction.

### Basic Compaction

```python
def compact(sub_tree_paths: list[str]):
    """Merge the specified sub-trees into one."""
    # 1. Read all annotations from the selected sub-trees
    all_annotations = []
    for path in sub_tree_paths:
        all_annotations.extend(read_all_from(path))

    # 2. Apply tombstones / dedup by ID (latest writer wins)
    live = deduplicate(all_annotations)

    # 3. Write a fresh precomputed tree using cloud-volume
    new_path = f"trees/{next_seq()}/"
    write_precomputed(new_path, live)

    # 4. Atomic manifest swap: replace merged sub-trees with the new one
    update_manifest(remove=sub_tree_paths, add=[new_path])

    # 5. Cleanup old sub-trees after TTL for in-flight reads
    schedule_cleanup(sub_tree_paths, delay=300)
```

Compaction reuses **exactly the existing cloud-volume write path**. The compacted sub-tree has correct capacity-based level assignment, correct progressive loading, and no duplicates.

> **Memory caveat:** The basic compaction pseudocode loads all annotations into memory. For large datasets, this is infeasible. See [Memory-Efficient Compaction](#memory-efficient-compaction) below.

### Compaction Strategies

Because compaction is just "merge some sub-trees into fewer sub-trees," it can be applied flexibly:

- **Full compaction:** Merge all sub-trees into one. Produces optimal read performance (single sub-tree, no fan-out). Equivalent to the current full materialization write step.
- **Partial compaction:** Merge only the smallest/oldest sub-trees, leaving large ones untouched. Reduces sub-tree count without rewriting the entire dataset. Analogous to LSM tiered compaction.
- **Spatial compaction:** Merge sub-trees that overlap spatially. Useful when many small sub-trees cover the same region.

### Memory-Efficient Compaction

For datasets with tens of millions of annotations (tens of GB), holding all annotation data in memory is infeasible. The bottleneck is the capacity-based level-assignment algorithm: it needs per-cell annotation counts at every level to decide overflow.

However, the algorithm only needs annotation **positions** (to compute cell membership) and **counts** (to decide overflow) — not the full annotation records. This enables streaming compaction.

#### Strategy 1: Two-Pass Count-Then-Write (Recommended)

**Pass 1 — Count (streaming, bounded memory):** Stream all annotations from the sub-trees being compacted. For each annotation, compute its cell at each level from its (x, y, z) position. Maintain an in-memory map of `(level, cell) → count`. Discard annotation data after updating counts.

Run the capacity-based assignment algorithm on the count map to determine, for each cell at each level, how many annotations it should accept before overflowing. This produces a threshold map: `(level, cell) → capacity_remaining`.

**Pass 2 — Assign and write (streaming, bounded memory):** Stream all annotations again. For each, walk the level hierarchy (coarsest to finest) to find the first level with remaining capacity (decrementing the threshold map). Write the annotation directly to the output chunk file for its assigned level/cell. Buffer at most one chunk's worth of annotations at a time (bounded by `limit`).

**Memory:** Cell-count map (~hundreds of MB for 100M annotations across 10 levels) + one chunk buffer (bounded by `limit`). Full annotation data is never in memory.

**Trade-off:** Two full streaming scans of the input sub-trees (2× network reads from cloud storage). Data is read sequentially and streams efficiently.

#### Strategy 2: Spatial Partitioning (Parallel-Friendly)

Divide the volume into coarse spatial regions (e.g. level-2 or level-3 grid cells). Compact each region independently:

1. For region R, stream only annotations whose position falls within R.
2. Run level assignment within R.
3. Write output chunks for R.

Each region is a fraction of the full dataset. Regions are independent and can run **in parallel** across workers.

**Caveat:** Levels 0 and 1 span multiple regions. Handle these with a lightweight first pass using only counts, then process finer levels per-region.

**Memory:** One region's annotation data + coarse-level count map. For a level-3 partition, each region is 1/512 of the volume.

**Trade-off:** More complex orchestration (partition assignment, coarse-level merge). But naturally parallel and well-suited to distributed compaction workers.

#### Strategy 3: External Sort by Morton Code

Sort all annotations by their 3D Morton code (Z-order curve over x, y, z) using disk-backed external merge sort. Stream through sorted output — annotations in the same cell are contiguous, so each cell is processed sequentially with bounded memory.

**Memory:** Configurable sort buffer (e.g. 1–4 GB). Temporary disk proportional to dataset size.

**Trade-off:** Most general — works at any scale with fixed memory. But adds disk I/O and merge-sort complexity. Best for extremely large datasets (100M+ annotations) where even Strategy 1's count map is a concern.

#### Recommendation

**Strategy 1 (two-pass)** is sufficient for most CAVE datasets and simplest to implement. Strategy 2 if compaction is already distributed. Strategy 3 as a fallback for extreme scale.

## Performance Characteristics

**Write latency:** A sub-tree write is a standard cloud-volume upload (seconds for 10K annotations) plus an atomic manifest update. Much faster than rewriting a multi-million annotation tree.

**Read latency:** Each spatial chunk request fans out to N sub-trees in parallel. With ≤20 sub-trees, overhead is modest. Manifest and coarse-level chunks should be cached in the adapter.

**Compaction cost:** Proportional to the annotations being merged. Full compaction is equivalent to the current materialization write step. Partial compaction can be much cheaper.

**Concurrent writers:** Fully supported. Each writer produces an independent sub-tree. Manifest updates are the only serialization point.

**Memory:** Writers and compactors can operate with bounded memory using spatial partitioning or streaming strategies. No requirement to hold the full dataset in RAM.

### Read Fan-Out: Request Count Scaling

The primary cost of this design is that **every Neuroglancer request is amplified by the number of sub-trees**. This warrants detailed analysis because Neuroglancer's access pattern is bursty and concurrent.

**Per-chunk request cost:** A single spatial chunk request becomes N parallel GCS GETs, where N = number of sub-trees. Most of these will return 404 (empty chunk in that sub-tree), which is fast (~10–30ms on GCS), but each still incurs a network round-trip.

**Neuroglancer's access pattern amplifies this further.** On initial layer load, Neuroglancer requests chunks from all levels simultaneously. For a 10-level hierarchy:

| Level | Grid cells | Requests per level |
|-------|------------|-------------------|
| 0 | 1 | 1 |
| 1 | 8 | 8 |
| 2 | 64 | 64 |
| 3 | 512 | ~50–100 (viewport-dependent) |
| ... | ... | ... |

A typical initial viewport might generate ~100–200 chunk requests across all levels. With N sub-trees, that becomes **100–200 × N GCS GETs**. At 20 sub-trees: 2,000–4,000 GCS requests on initial load.

**Panning and zooming** generates additional chunk requests for newly visible cells, each amplified by N.

**Concrete latency estimates** (assuming GCS, p50 latency per GET):

| Sub-trees (N) | Requests per chunk | p50 latency (parallel) | p99 tail (parallel) |
|---------------|-------------------|----------------------|-------------------|
| 1 | 1 | ~30ms | ~80ms |
| 5 | 5 | ~40ms | ~120ms |
| 20 | 20 | ~60ms | ~200ms |
| 50 | 50 | ~100ms | ~400ms |

Parallel fan-out means latency grows sub-linearly (dominated by the slowest response), but tail latency grows faster — with N requests, the probability of at least one slow response increases.

**Mitigations:**

1. **Cache 404s.** Most sub-trees are spatially sparse — a given chunk exists in only a few sub-trees. After the first request, cache which sub-trees have data for which coarse cells. This eliminates most fan-out for warm paths.
2. **Sub-tree spatial metadata.** Store each sub-tree's bounding box in the manifest. Skip sub-trees whose bounds don't overlap the requested chunk. For spatially-partitioned sub-trees, this reduces fan-out to ~1–2 per request.
3. **Coarse-level caching.** Levels 0–3 have few cells and are requested on every viewport change. Cache their merged results in the adapter. This eliminates fan-out for the most frequently requested chunks.
4. **Compaction as the primary control.** Keep sub-tree count bounded. The fan-out problem is self-correcting if compaction runs at a reasonable cadence relative to the append rate.

**GCS request cost:** At $0.004 per 10K Class B operations (GETs), 4,000 GETs per initial load is $0.0016. Not negligible at scale if many users are loading the layer simultaneously, but manageable. The latency cost is more concerning than the monetary cost.

## What This Replaces

```
# Current pipeline
PostgreSQL → MaterializationEngine → cloud-volume (full rewrite) → GCS → Neuroglancer

# Proposed pipeline
Any writer → cloud-volume (sub-tree write) → GCS → HTTP adapter → Neuroglancer
                        ↑                                  ↓
                        └──── compaction (periodic) ───────┘
```

The full-rewrite step is replaced by sub-tree writes. Compaction runs on a schedule to merge sub-trees. The HTTP adapter is the only new component.

## Incremental Adoption: Client-Side Prototyping and Native Neuroglancer Support

### Short-Term: Multiple Annotation Layers in Neuroglancer

The HTTP adapter can be prototyped without any server-side code by adding each sub-tree as a **separate precomputed annotation layer** in the Neuroglancer viewer. Each sub-tree is already a valid, self-contained precomputed annotation source — Neuroglancer can load it directly.

For example, a viewer state with 3 sub-trees:

```json
{
  "layers": [
    {"type": "annotation", "source": "precomputed://gs://bucket/.../trees/0001"},
    {"type": "annotation", "source": "precomputed://gs://bucket/.../trees/0002"},
    {"type": "annotation", "source": "precomputed://gs://bucket/.../trees/0003"}
  ]
}
```

This is a client-side mock of what the HTTP adapter would do server-side: each sub-tree is loaded and rendered independently, and the visual result is the union of all sub-trees. The user sees all annotations. No adapter needed.

**Limitations of the client-side approach:**
- No dedup or tombstone handling across layers (updates/deletes don't shadow older versions)
- Each layer has its own progressive-loading hierarchy (the `limit` overshoot issue applies per-viewport)
- Layer count is visible to the user (UI clutter if many sub-trees)
- By-ID lookups don't work across layers (CAVEclient or other consumers would need to know which sub-tree an annotation lives in)

But for prototyping, demos, and low-sub-tree-count use cases, this works immediately with zero infrastructure.

### Long-Term: Native Neuroglancer Multi-Source Annotations

Neuroglancer could natively support the sub-tree merging concept — a single annotation layer backed by a manifest of multiple precomputed sources, with client-side merge. This would:

- Eliminate the HTTP adapter entirely for the common case
- Push merge logic into the viewer's existing progressive-loading pipeline
- Enable `limit`-aware merging at render time (the viewer already tracks per-cell annotation counts for its rendering pipeline)
- Support tombstones/dedup natively if the manifest includes ordering metadata

This would be a relatively small extension to Neuroglancer's annotation data source: instead of one `precomputed://` URL, accept a manifest URL that lists multiple sources plus merge semantics. The existing spatial chunk fetch, decode, and render pipeline handles each source identically — the only new logic is the merge step.

## What This Does Not Provide

- **Columnar analytical access.** The precomputed format is binary, not Parquet/Arrow. SQL queries over annotations still require a separate store (PostgreSQL, a lake table, etc.). The two can coexist.
- **Real-time append visibility.** A new sub-tree takes ~1–5s (cloud-volume upload + manifest update). Not sub-second, but much faster than a full materialization cycle.
- **Infinite sub-trees without compaction.** Read latency scales with sub-tree count. Keep it bounded via compaction. In practice, ≤20 sub-trees is manageable.

## Comparison to Delta Lake Approach

An [alternative proposal](delta-precomputed-annotations.md) considers serving Neuroglancer directly from a Delta Lake table with spatial cell columns and ZORDER clustering. That approach offers a unified analytical + serving layer but introduces:

- A new storage format (Delta Lake / Parquet) and query engine
- Unsolved progressive-loading semantics (`display_level` hash produces unbounded coarse-level density)
- Read amplification from filtering on a uniformly-distributed column within ZORDER-clustered files
- Higher query latency (Delta transaction log + Parquet decode vs. raw GCS object GET)
- Explicit `OPTIMIZE ZORDER` scheduling with degraded serving between runs
- Open problems for by-ID lookups and relationship indexes on Delta

The sub-tree merging approach solves the specific goal — appendable, concurrent, memory-bounded precomputed annotations — with:

- ~200 lines of adapter code (FastAPI + asyncio + cloud-volume for reads)
- A precomputed annotation writer (new, required — cloud-volume's read-side code and format knowledge can inform the implementation)
- Zero new storage format dependencies
- Compaction reuses the same writer used for sub-tree construction

If a unified analytical + serving layer is desired long-term, the Delta Lake approach (or alternatives like Lance or DuckDB + GeoParquet) may warrant further investigation. But for the immediate goal of incremental, concurrent-writer-safe, memory-bounded annotation publishing to Neuroglancer, this is a smaller and lower-risk path.

## Open Questions

- **Manifest atomicity on S3.** GCS has atomic single-object writes, making manifest updates safe. S3's conditional writes (added 2024) provide similar guarantees but need testing. Alternatively, use DynamoDB or a lightweight coordination service for the manifest on AWS.
- **Sub-tree count threshold for compaction.** Needs benchmarking: how many parallel reads per chunk request are acceptable before latency degrades? Initial guess: compact when sub-tree count exceeds 10–20.
- **Tombstone representation.** Need a convention for encoding deletes. Options: a sentinel property value, a sidecar `tombstones.json` per sub-tree listing deleted annotation IDs, or a dedicated "tombstone sub-tree" containing only IDs to exclude.
- **Relationship indexes.** The precomputed format supports optional relationship indexes (`{rel_key}/{related_id}`). These need the same sub-tree merge treatment. Since relationship lookups are by a single related ID, the reverse-scan approach (same as by-ID) works directly.
- **Overlay-aware construction (Option C) at scale.** If many sub-trees exist, reading all their per-cell count metadata to build a new sub-tree may become costly. A cached aggregate count index (updated at compaction time) could help.
