# Serving Lake Tables (DeltaLake / Lance / Iceberg) as Neuroglancer Precomputed Annotations

> Last investigated: 2026-04-07

## The Precomputed Annotation Format

A precomputed annotation source is a file/HTTP endpoint tree anchored at a base URL. Neuroglancer expects three types of index, all described by a root `info` JSON:

| Index type | Path (unsharded) | Purpose |
|---|---|---|
| ID index | `by_id/{uint64_id}` | Fetch one annotation by its uint64 ID |
| Spatial index | `spatial/{level}/{x}_{y}_{z}` | Fetch all annotations in a grid cell at a given hierarchy level |
| Relationship index | `{rel_key}/{uint64_related_id}` | Fetch annotations linked to a related object (e.g., a segment) |

The `info` JSON declares `dimensions`, `lower_bound`, `upper_bound`, `annotation_type` (one of `POINT`, `LINE`, `AXIS_ALIGNED_BOUNDING_BOX`, `ELLIPSOID`, `POLYLINE`), typed `properties`, `relationships`, and one entry per spatial hierarchy level. Binary records use little-endian float32 geometry, packed typed properties (uint8–float32, rgb, rgba), and uint64 IDs.

Cloud-volume's `cloudvolume/datasource/precomputed/annotation/` module implements the full spec and is a useful implementation reference (or direct dependency).

## What an Adapter Must Do

Given a lake table with columns `(annotation_id, x, y, z, ...properties...)`, the adapter must:

1. **Introspect the schema** → build the `info` JSON (bounds from min/max of x/y/z, properties map from column dtypes).
2. **Serve by-ID**: filter `annotation_id = ?` → pack one binary record.
3. **Serve spatial chunks**: return annotations for each `(level, grid_cell)` pair → pack N-record binary chunk.
4. (Optionally) **Serve relationship index**: filter by a foreign-key column.

Binary encoding is not hard — cloud-volume's Python implementation is the reference; packing float32 geometry + typed properties is ~50 lines of numpy/struct code. The `info` JSON is trivial to generate from any schema-aware lake table reader. CORS must be enabled if Neuroglancer is served from a different origin, and token-based auth (e.g., `middle_auth_client`) must be added at the adapter layer if data is sensitive (the precomputed format has no built-in auth).

## Spatial Index Strategy Options

The main design decision is how the lake table encodes the spatial hierarchy needed to serve `spatial/{level}/{x}_{y}_{z}` requests. Three strategies are possible, each suited to different write patterns and scale requirements.

---

### Option A — On-the-Fly Bbox Queries

The lake table stores a flat `(annotation_id, x, y, z, ...properties...)` schema with no spatial index columns. Each spatial chunk request triggers a bounding box predicate query at serve time.

**How it works:**
- The adapter computes the bbox of the requested grid cell and issues a range-filter query.
- All three lake formats support predicate pushdown: DeltaLake skips row groups outside the bbox (especially efficient with `OPTIMIZE ZORDER BY (x, y, z)`); Lance has native spatial query support; Iceberg with PyArrow pushes predicates to sorted manifests.
- Without spatial clustering, any bbox query is a full scan — workable for moderate sizes if the server is co-located with storage.

**No spatial index needed** for small datasets (≤500K annotations); a single `grid_shape: [1,1,1]` level (whole table = one chunk, full-scan, cached in memory) handles most per-neuron/per-region use cases with 2–4 days of implementation work. Multi-level hierarchy on the fly adds 1–2 weeks.

**Tradeoffs:**
- Writes: trivially easy — plain append, no extra columns
- Reads: range scan per request; efficiency depends on clustering
- Incremental updates: easy
- Analytical queries: easy — table schema is clean

**Best fit:** Simple datasets, fast prototyping, or tables already used for analysis where adding index columns would be disruptive.

---

### Option B — Pre-Partitioned Lake (Capacity-Limited Assignment)

If you control lake construction, pre-assign each annotation to exactly one `(level, gx, gy, gz)` cell using a coarsest-first, capacity-limited algorithm that mirrors how neuroglancer builds its spatial index. Partition the Delta table by those columns. Serving a spatial chunk becomes a direct partition lookup with no range scan.

**Assignment algorithm (run at write/materialization time):**
1. Level 0 (`grid_shape [1,1,1]`): uniformly sample up to `limit` annotations → assign `(level=0, gx=0, gy=0, gz=0)`.
2. Level 1 (`grid_shape [2,2,2]`): for each of the 8 cells, take unassigned annotations in that cell's bbox, sample up to `limit` → assign.
3. Repeat to finest level; all remaining unassigned annotations go there.

In polars: a series of `filter + sample + anti-join` passes, parallelizable per-cell.

**Schema:** One table per level (`annotations_l0`, `annotations_l1`, ...) partitioned by `(gx, gy, gz)`, mirroring neuroglancer's `spatial/{level}/` path structure. Alternatively one table with `PARTITION BY (level, gx, gy, gz)`.

**What serving looks like:**
```
GET /spatial/2/3_1_0
  → filter annotations_l2: gx=3, gy=1, gz=0
  → transaction log resolves to exact parquet files — no scan
  → pack binary response
```

A pre-packed `blob: bytes` column per partition can reduce serving to a single lookup returning raw bytes — lowest latency, but the lake becomes opaque to analytical queries.

**Tradeoffs:**
- Writes: expensive — assignment algorithm requires anti-join against existing rows; not compatible with row-by-row appends
- Reads: O(1) partition lookup — fastest possible
- Incremental updates: complex; new annotations may need level rebalancing
- Analytical queries: awkward — level/cell columns are noise for analysis

**Best fit:** Batch-written datasets read many times. Consistent with how CAVE materialized annotation tables are already produced — the assignment pipeline runs once per materialization cycle.

---

### Option C — Morton Code / Implicit Octree Membership

Store each annotation's position as a Morton code (Z-order curve index over x/y/z). The cell at each hierarchy level is a bit-prefix of the Morton code, so spatial membership at any level is implicit — no assignment algorithm needed.

**Concept:**
```python
cell_at_level_k = morton_code >> (max_bits - k * bits_per_dim)
```

Store either one `morton_code: uint64` column (compact) or explicit per-level columns `(cell_l0, cell_l1, ...)` (enables hierarchical Delta partitioning). Both forms are isomorphic.

**The duplicate problem:** Every annotation is a member of its cell at *every* level — a naive server would render each annotation multiple times. Neuroglancer does not deduplicate across levels. Fix with a `display_level` column, a pure function of annotation ID:

```python
display_level = hash(annotation_id) % num_levels  # or weighted toward finer levels
```

A level-k request for cell C filters: `cell_lk = C AND display_level = k`. Because `display_level` depends only on `annotation_id`, it requires no global coordination and is stable across appends.

**Partition scheme limits:** Hierarchical Delta partitioning on `(cell_l0, cell_l1, ..., cell_lN)` works in principle, but leaf partition count is `8^N` — at 10 levels this is ~10^9. In practice, cap explicit partitioning at 3–5 coarse levels and rely on ZORDER clustering on `morton_code` for finer levels. ZORDER on `morton_code` is the natural fit since it is already the ideal Z-order key.

**Tradeoffs:**
- Writes: **easiest of all options** — two cheap computed columns (`morton_code`, `display_level`) are pure functions of position and ID; appends are trivially parallel and stateless
- Reads: Morton range filter + `display_level` equality; slightly more files touched than an exact partition match, but comparable to a well-clustered Option A table
- Incremental updates: **trivially easy** — no inter-row coordination needed
- Analytical queries: `morton_code` is useful for spatial queries; `display_level` is ignorable metadata

**Best fit:** Datasets with frequent incremental appends or where writes must remain stateless. The `display_level` column is the one non-obvious addition, but it is the only thing keeping level assignments stable and duplicate-free without global state.

---

## Comparison Summary

| Property | A: On-the-fly bbox | B: Pre-partitioned (capacity-limited) | C: Morton code / implicit |
|---|---|---|---|
| Write simplicity | Simple append | Hard — anti-join per level | **Easiest** — pure function per row |
| Incremental appends | Easy | Requires rebalancing pipeline | **Trivially easy** |
| Read pattern | Range scan (efficiency depends on clustering) | O(1) partition lookup | Morton range + display_level filter |
| Duplicate-free by construction | Yes (one level per query) | Yes | Requires `display_level` column |
| Partition cardinality | N/A | Low — bounded by grid cells per level | High — must cap at ~5 explicit levels |
| Analytical usability | **Best** — clean schema | Awkward — level/cell columns are noise | Good — Morton code is useful; display_level is ignorable |
| Best fit | Prototyping; analysis-primary tables | Batch-written, read-heavy datasets | Append-heavy or stateless write pipelines |
