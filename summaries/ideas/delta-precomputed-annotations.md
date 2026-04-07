# Idea: Delta Lake as Neuroglancer Precomputed Annotation Index via Octree Cell Encoding

## Background: The Neuroglancer Precomputed Annotation Format

Neuroglancer can load annotations (points, lines, bounding boxes, ellipsoids) from any HTTP server that implements the "precomputed" annotation format. The server exposes a tree of files rooted at a base URL:

- **`info`** — a JSON file declaring the annotation type, coordinate space, typed per-annotation properties (e.g. `confidence: uint8`, `label: rgba`), and the structure of the spatial index.
- **`by_id/{annotation_id}`** — binary record for a single annotation fetched by its uint64 ID.
- **`spatial/{level}/{gx}_{gy}_{gz}`** — binary chunk containing all annotations belonging to grid cell `(gx, gy, gz)` at hierarchy level `level`. Neuroglancer fetches these progressively as the user navigates.
- **`{rel_key}/{related_id}`** — (optional) index of annotations linked to a related object such as a segment ID.

Binary records are packed little-endian: float32 geometry, typed property values, uint64 IDs. The format is well-specified and cloud-volume's Python implementation (`cloudvolume/datasource/precomputed/annotation/`) is a working reference.

### The Spatial Hierarchy

The spatial index is a coarse-to-fine octree. The `info` JSON defines multiple levels, each with a `grid_shape` (e.g. `[1,1,1]` at level 0, `[2,2,2]` at level 1, `[4,4,4]` at level 2, ...) and a `limit` (max annotations per chunk). Neuroglancer loads coarse levels first for an overview, then requests finer cells as the user zooms in. Each annotation appears at **exactly one level** — the coarsest level where its containing cell is not yet full — so annotations are not rendered twice.

Neuroglancer itself does not deduplicate: if a server returns the same annotation at multiple levels, it will be rendered multiple times. Correct level assignment is therefore the server's responsibility.

## The Problem

CAVE annotation data is increasingly stored in columnar lake formats (DeltaLake, Lance, Iceberg) in cloud object storage — both for analytical queries and as a durable record of materialized annotations. Serving this data to Neuroglancer today requires an extra step: write a full copy to GCS in precomputed format using cloud-volume. This is redundant if the lake is already the source of truth.

The goal is a thin HTTP adapter that serves Neuroglancer precomputed annotations **directly from the lake table**, with the lake schema designed so that each spatial chunk request maps efficiently to a lake query — without maintaining a separate GCS copy.

This will also facilitate appendable precomputed annotations, which do not currently exist. This will make it easier for distributed
systems to be able to write to neuroglancer precomputed.

## Proposal: Octree Cell Columns + Delta ZORDER

The cell at each level of the neuroglancer spatial hierarchy is a pure function of position. For a uniform octree over x/y/z, cell membership at level k is determined by dividing the bounding box into a `2^k × 2^k × 2^k` grid and finding which grid cell the point falls into — equivalently, the top `k × bits_per_dim` bits of the Morton code (Z-order curve index over x/y/z):

```python
cell_at_level_k = bit_interleave(x, y, z) >> (max_bits - k * bits_per_dim)
```

This means spatial membership at every level is a pure function of position — no assignment algorithm, no inter-row coordination, no global state. Compute a small number of these (`cell_l0`, `cell_l1`, ...) as integer columns at write time and store them in the Delta table.

Note: there is no need to store the Morton code itself as a column. Delta Lake's `OPTIMIZE ZORDER BY (x, y, z)` already computes the Z-order curve over those three columns internally and physically reorders parquet row groups to cluster spatially nearby rows. This gives the same file-layout benefit without an extra column. The `cell_lk` columns are needed only to enable coarse-level **partition pruning** (direct lookup via the transaction log); for fine-level queries within a partition, ZORDER on x, y, z handles row-group skipping.

A thin FastAPI adapter translates each Neuroglancer spatial chunk request into a Delta table filter on the appropriate `cell_lk` partition column.

### The Duplicate Problem and Fix

Because every annotation is a member of its cell at *every* level, a naive server would return the same annotation in response to requests at multiple levels, causing Neuroglancer to render it multiple times.

Fix with a `display_level` column, a pure function of annotation ID computed at write time:

```python
display_level = hash(annotation_id) % num_levels  # or weighted toward finer levels
```

A level-k request for cell C then filters:

```sql
cell_lk = C AND display_level = k
```

Because `display_level` depends only on `annotation_id`, it requires no global coordination and is stable across appends — new rows never change the display level of existing rows.

A weighted distribution (e.g. fewer annotations assigned to coarse levels) can be used to produce a more visually appropriate progressive-loading experience, matching the density behavior of the capacity-limited algorithm.

### Partition Scheme

Hierarchical Delta partitioning on `(cell_l0, cell_l1, ..., cell_lN)` works in principle, but the number of leaf partitions is `8^N` — at 10 levels this is ~10^9, far too many. In practice:

- Store **explicit cell columns** only for 3–5 coarse levels (manageable partition count; requests for those levels hit the transaction log directly with no scan).
- For finer levels, issue bbox predicates on x, y, z and rely on **`OPTIMIZE ZORDER BY (x, y, z)`** for row-group skipping. Delta computes the Z-order curve over those columns internally — no extra column needed.

## Schema

```
annotation_id: uint64
x, y, z: float32           -- raw position
cell_l0: int32             -- coarse octree cell index at level 0, computed from x,y,z
cell_l1: int32
...cell_l4: int32          -- stop here; ZORDER handles finer levels
display_level: int8        -- hash(annotation_id) % num_levels
...properties...
```

`PARTITION BY (cell_l0, ..., cell_l4)` + `OPTIMIZE ZORDER BY (x, y, z)`.

## Key Properties

- **Writes:** The cell columns and `display_level` are pure functions of position and ID — no anti-joins, no global state, no capacity checks. Appends are trivially parallel and stateless. Can be computed row-by-row at ingest time.
- **Reads:** Coarse-level requests use partition pruning (direct transaction log lookup); fine-level requests use bbox predicates on x, y, z with ZORDER row-group skipping.
- **Incremental updates:** Trivially easy — new rows are entirely independent of existing ones.
- **Analytical queries:** x, y, z remain as plain columns; ZORDER benefits spatial analytical queries too; `display_level` is ignorable metadata for non-serving consumers.

## What This Replaces

This approach replaces the cloud-volume write step in the materialization pipeline:

```
# Current
lake table → cloud-volume write → GCS precomputed files → Neuroglancer

# Proposed
lake table (cell_lk + display_level columns, ZORDER BY x,y,z) → thin HTTP adapter → Neuroglancer
```

The GCS precomputed copy is eliminated. The lake table remains the single source of truth and is still queryable analytically.

## Open Questions

- **Display level weighting:** A uniform `hash(id) % N` distribution puts equal fractions of annotations at each level regardless of the octree's structure. A spatially-aware weighting (e.g. proportional to expected cell density) might produce better progressive-loading behavior but requires knowing global density at write time. One mitigation is to ask the user for an estimate of total number of points, and have the code compute approximate weighting distributions to set approximate densities.
- **`by_id` index:** Unless `annotation_id` is also the Delta partition key (unlikely), by-ID lookups still require a scan or a secondary index. Similar to how neuroglancer precomputed works, we can implement this as a separate small Delta table keyed by `annotation_id`.
- **Relationship index:** Straightforward to add as a separate Delta table `(related_id, annotation_id)` partitioned by `related_id`.
- **Explicit Morton code column:** We considered storing `morton_code: uint64` as a column to enable range-based cell queries at all levels. This seems unnecessary because Delta Lake's `OPTIMIZE ZORDER BY (x, y, z)` handles physical clustering over those dimensions internally, making a stored Morton code redundant. However, the interaction between Delta's ZORDER implementation and fine-level spatial query efficiency warrants more investigation — it's possible that an explicit Morton code column enables tighter range predicates than separate x/y/z bbox filters in some scenarios.

## Known Issues and Risks

### `display_level` produces unbounded coarse-level density

The hash-based `display_level = hash(id) % N` distributes annotations **uniformly across levels**, not spatially. This breaks the core promise of the precomputed format's progressive loading.

In the capacity-based algorithm, coarse cells are bounded at `limit` annotations and overflow spills to finer cells. At coarse zoom you see a spatially representative sample; zooming in reveals more detail in dense regions. With the hash approach:

- A coarse-zoom request for a cell containing 1M annotations returns ~1M/N annotations — **unbounded**, with no per-cell density cap.
- Dense and sparse regions are treated identically — the "overview" at level 0 is a random 1/N sample, not a spatially representative one.
- As annotations are appended, coarse chunks grow without bound; the capacity-based algorithm naturally pushes new annotations to finer levels.

The precomputed `limit` field is specifically a visual density guarantee. This design discards it.

### `display_level` filter causes read amplification at fine levels

For level-k requests handled by ZORDER (levels 5+), the filter is `bbox(x,y,z) AND display_level = k`. ZORDER clusters by `(x, y, z)`, so spatially-relevant row groups are selected efficiently. But `display_level` is uniformly distributed — **every spatially-relevant row group contains all display_level values**. The filter fires in-memory only after decompression. The query reads N× more data than needed (where N = num_levels).

### Delta Lake query latency may be too high for interactive serving

Delta's query pipeline — log scan → file pruning → Parquet network I/O → row group filtering — is optimized for analytical batch workloads, not sub-second HTTP responses under concurrent load. Each Neuroglancer viewport generates multiple simultaneous chunk requests. Expect 200ms–2s+ per query on cloud storage. The existing GCS precomputed format is a raw object-store GET at ~20–80ms. This latency difference is significant for interactive use.

The adapter would need to cache:
- The parsed Delta transaction log (expensive to re-read per request).
- Coarse-level query results (hot paths; same chunks requested repeatedly as users pan).

### `OPTIMIZE ZORDER` must be scheduled explicitly

Delta's Z-ORDER reordering is not applied on write — it must be triggered via `OPTIMIZE`. Fresh appends land in unoptimized files. Until OPTIMIZE runs, spatial queries fall back to file-level scans. The design needs a scheduling answer: who triggers OPTIMIZE, how often, and what is the serving behavior during the window between append and compaction?

### `num_levels` is immutable at write time

`display_level = hash(id) % num_levels` bakes `num_levels` into every row. If the spatial hierarchy changes (more levels added as the dataset grows, or levels adjusted for a different volume), a full table rewrite is required. The capacity-based algorithm has the same constraint, but this design does not offer any additional flexibility here.

### Relationship index partition explosion

"Partition by `related_id`" for the segment-annotation relationship index creates one partition per unique related object. For synapse tables with 10M+ unique segment IDs, this means millions of partitions, each containing a handful of rows — the classic Delta small-file pathology. It would need aggressive `OPTIMIZE` runs, and even then Delta's metadata overhead per partition is non-trivial. A separate sorted Parquet file range-partitioned by `related_id`, or a proper inverted index, is likely better suited.

### `by_id` lookup is a key-value access pattern Delta isn't suited for

By-ID lookups require returning a single annotation binary in milliseconds. A separate Delta table keyed by `annotation_id` still requires reading the transaction log plus at least one Parquet file per lookup. Delta is not a key-value store. Alternatives to evaluate: sorted Parquet with `annotation_id` as a hash-bucket partition key, a sidecar SQLite or Redis index, or keeping the existing per-file-per-ID layout for by-ID while using Delta only for spatial queries.

### Composite partition key requires ancestor filters

`PARTITION BY (cell_l0, ..., cell_l4)` is a composite key, not a hierarchy. Querying `cell_l2 = X` without also specifying `cell_l0` and `cell_l1` scans all parent-level partition directories. Since cell level values are nested (each `cell_l2` maps to exactly one `cell_l1` and `cell_l0`), the adapter must include all ancestor cell values in the filter. This is derivable from position but must be implemented correctly or partition pruning is lost.

### Neuroglancer concurrent level loading

Neuroglancer requests chunks from all levels simultaneously when an annotation layer is first loaded. The adapter must handle a burst of concurrent requests across all levels — not just one level at a time. Fine-level requests may hit expensive ZORDER-unoptimized files if traffic spikes right after an append.

### No cache-busting / versioning for HTTP responses

With GCS precomputed, the URL encodes the materialization version (`/mat1412/`). A browser or Neuroglancer can cache responses by URL indefinitely. With a live Delta table serving reads, the same URL returns different data as rows are appended. The adapter needs a caching strategy (ETags, version query params, or short TTLs) to avoid serving stale cached responses.

## Alternative Approaches

See [appendable-precomputed.md](appendable-precomputed.md) for an alternative design that achieves appendable precomputed annotations by representing a logical annotation layer as an ordered list of standard precomputed sub-trees merged at read time, without introducing a new storage format.
