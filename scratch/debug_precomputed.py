# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "caveclient[cv]>=8.0.1",
#     "ipykernel>=7.2.0",
#     "ipywidgets>=8.1.8",
#     "nglui",
#     "polars>=1.39.3",
#     "tensorstore>=0.1.82",
# ]
#
# [tool.uv.sources]
# nglui = { path = "../submodules/nglui", editable = true }
# ///


# %%
import atexit
import http.server
import socket
import threading
from pathlib import Path

import polars as pl
from caveclient import CAVEclient
from nglui.precomputed import PointAnnotationWriter
from nglui.statebuilder import ViewerState

# %% LOAD SYNAPSE TABLES

print("Loading synapse table...")
table_path = Path("/Users/ben.pedigo/code/meshrep/meshrep/data/mega_tables")
synapses_pl = pl.read_parquet(table_path / "filtered_synapses.parquet")
synapses = synapses_pl.to_pandas()
# root_ids = [
#     864691135489514810,
#     864691135495542672,
#     864691136991202453,
#     864691136662432990,
#     864691136311213914,
# ]

# synapses = synapses.query("post_pt_root_id.isin(@root_ids)")
print("Done loading synapse table.")


# %%

print("Initializing CAVE client...")
client = CAVEclient("minnie65_public")
print("Done initializing CAVE client.")

print("Writing precomputed data...")
writer = PointAnnotationWriter(
    segmentation_source=client,
    point_column=["ctr_pt_position_x", "ctr_pt_position_y", "ctr_pt_position_z"],
    property_columns=[
        "size",
        "pre_cell_type",
        "post_cell_type",
        "pre_in_selection",
        "post_in_selection",
        "tag_detailed",
    ],
    relationship_columns=["pre_pt_root_id", "post_pt_root_id"],
    id_column="synapse_id",
    data_resolution=[4, 4, 40],
    write_sharded=True,
    limit=10_000,
)

# out_path = "/Users/ben.pedigo/code/meshrep/meshrep/data/test_local_precomputed"
# out_path = "gs://allen-minnie-phase3/bdp-synapse-mega-tables/column-precomputed-test"
out_path = "tmp/precomputed/column-precomputed"

# clear out_path if it exists
if not out_path.startswith("gs://") and Path(out_path).exists():
    import shutil

    shutil.rmtree(out_path)

writer.write(synapses, out_path)
print("Done writing precomputed data.")

# %%
from nglui.precomputed import LineAnnotationWriter

line_writer = LineAnnotationWriter(
    segmentation_source=client,
    point_a_column=["pre_pt_position_x", "pre_pt_position_y", "pre_pt_position_z"],
    point_b_column=["post_pt_position_x", "post_pt_position_y", "post_pt_position_z"],
    property_columns=[
        "size",
        "pre_cell_type",
        "post_cell_type",
        "pre_in_selection",
        "post_in_selection",
        "tag_detailed",
    ],
    relationship_columns=["pre_pt_root_id", "post_pt_root_id"],
    id_column="synapse_id",
    data_resolution=[4, 4, 40],
    write_sharded=True,
    limit=10_000,
)
out_path = "gs://allen-minnie-phase3/bdp-synapse-mega-tables/column-precomputed-test-line"

line_writer.write(synapses, out_path)
print("Done writing line precomputed data.")

# %%

# # print the resulting info JSON metadata
# import json

# info_path = Path(out_path) / "info"
# with open(info_path, "r") as f:
#     info = json.load(f)
# print(json.dumps(info, indent=2))

# %%

shader = """
#uicontrol vec3 soma color(default="cyan")
#uicontrol vec3 shaft color(default="yellow")
#uicontrol vec3 spine color(default="magenta")
#uicontrol vec3 multi_spine color(default="purple")
#uicontrol float scale slider(min=0.0, max=10.0, default=3.0)
#uicontrol float opacity slider(min=0, max=1, default=1.0)
#uicontrol bool show_null checkbox(default=true)
#uicontrol bool show_spine checkbox(default=true)
#uicontrol bool show_multi_spine checkbox(default=true)
#uicontrol bool show_shaft checkbox(default=true)
#uicontrol bool show_soma checkbox(default=true)

void main() {
  setColor(defaultColor());
  setPointMarkerSize(float(prop_size()) * 0.0001 * scale);
  float alpha = (prop_pre_in_selection() == uint(1)) ? 1.0 : opacity;
  if (prop_tag_detailed() == uint(0)) {
    if (!show_null) { discard; }
    setColor(vec4(1.0, 1.0, 1.0, alpha));
  } else if (prop_tag_detailed() == uint(3)) {
    if (!show_multi_spine) { discard; }
    setColor(vec4(multi_spine, alpha));
  } else if (prop_tag_detailed() == uint(4)) {
    if (!show_shaft) { discard; }
    setColor(vec4(shaft, alpha));
  } else if (prop_tag_detailed() == uint(1) || prop_tag_detailed() == uint(2)) {
    if (!show_spine) { discard; }
    setColor(vec4(spine, alpha));
  } else if (prop_tag_detailed() == uint(5)) {
    if (!show_soma) { discard; }
    setColor(vec4(soma, alpha));
  }
  setPointMarkerBorderWidth(0.0);
}
"""

if out_path.startswith("gs://"):
    vs = (
        ViewerState(client=client)
        .add_layers_from_client()
        .add_annotation_source(
            f"precomputed://{out_path}",
            # relationship_columns=["pre_pt_root_id", "post_pt_root_id"],
            # shader=shader,
        )
    )
    vs.to_browser(shorten=True)

else:
    # Shut down any previously running server
    try:
        httpd.shutdown()
    except NameError:
        pass

    class CORSHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=out_path, **kwargs)

        def end_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            super().end_headers()

        def log_message(self, *args):  # silence request logs
            pass

    class ReuseServer(http.server.HTTPServer):
        allow_reuse_address = True

    with socket.socket() as s:
        s.bind(("", 0))
        addr = s.getsockname()[1]
    httpd = ReuseServer(("localhost", addr), CORSHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    atexit.register(httpd.shutdown)
    print(f"Serving on http://localhost:{addr}")

    vs = (
        ViewerState(client=client)
        .add_layers_from_client()
        .add_annotation_source(
            f"precomputed://http://localhost:{addr}",
            # relationship_columns=["pre_pt_root_id", "post_pt_root_id"],
        )
    )
    vs.to_browser()

    try:
        print("Server running. Press Ctrl+C to quit.")
        threading.Event().wait()
    except KeyboardInterrupt:
        httpd.shutdown()
        print("Server stopped.")


# %%

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class SpatialAssignment:
    """Records which level and cell a point was emitted at."""

    level: int
    cell: tuple[int, ...]


class SpatialIndexTree:
    """
    Builds and applies a neuroglancer-style spatial index hierarchy.

    Analogous to a scikit-learn fit/transform pattern:
    - fit(points) computes the hierarchy from data using the spec's
      randomised subsampling algorithm.
    - transform(points) deterministically assigns new points into the
      existing hierarchy by sampling `limit` points per cell at each
      level and pushing the rest down.

    Attributes (available after fit)
    --------------------------------
    metadata_ : list[dict]
        Per-level dicts with keys "key", "grid_shape", "chunk_size", "limit",
        matching the neuroglancer info JSON "spatial" array format.
    lower_bound_ : np.ndarray
    upper_bound_ : np.ndarray
    grid_shapes_ : list[np.ndarray]
        The grid shape at each level.
    """

    def __init__(
        self,
        limit: int = 4096,
        max_levels: int = 20,
        seed: int = 0,
    ):
        self.limit = limit
        self.max_levels = max_levels
        self.seed = seed

        # Set after fit
        self.metadata_: list[dict] = []
        self.lower_bound_: Optional[np.ndarray] = None
        self.upper_bound_: Optional[np.ndarray] = None
        self.grid_shapes_: list[np.ndarray] = []
        self._chunk_sizes: list[np.ndarray] = []
        self._fitted = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_chunk_size(self, grid_shape: np.ndarray) -> np.ndarray:
        return (self.upper_bound_ - self.lower_bound_) / grid_shape.astype(np.float64)

    def _cell_of(self, pts: np.ndarray, level: int) -> np.ndarray:
        """Return (len(pts), rank) int array of cell coordinates for a level."""
        cs = self._chunk_sizes[level]
        gs = self.grid_shapes_[level]
        coords = ((pts - self.lower_bound_) / cs).astype(int)
        np.clip(coords, 0, gs - 1, out=coords)
        return coords

    @staticmethod
    def _group_by_cell(
        indices: np.ndarray,
        cell_coords: np.ndarray,
    ) -> dict[tuple, np.ndarray]:
        """Group point indices by their cell coordinate tuple."""
        if len(indices) == 0:
            return {}
        groups: dict[tuple, list[int]] = {}
        for idx, coord in zip(indices, cell_coords):
            key = tuple(coord)
            groups.setdefault(key, []).append(idx)
        return {k: np.array(v, dtype=int) for k, v in groups.items()}

    def _build_grid_shapes(self, rank: int) -> list[np.ndarray]:
        """
        Generate the sequence of grid shapes from coarse to fine.

        Level 0 is all-ones. Each subsequent level doubles the axis with
        the largest physical chunk size (most anisotropic), producing
        increasingly isotropic cells.
        """
        extent = self.upper_bound_ - self.lower_bound_
        shapes: list[np.ndarray] = [np.ones(rank, dtype=int)]
        for _ in range(self.max_levels - 1):
            prev = shapes[-1]
            cs = extent / prev.astype(np.float64)
            axis = int(np.argmax(cs))
            new = prev.copy()
            new[axis] *= 2
            shapes.append(new)
        return shapes

    def _ensure_fitted(self):
        if not self._fitted:
            raise RuntimeError("Call fit() before transform().")

    # ------------------------------------------------------------------
    # fit
    # ------------------------------------------------------------------

    def fit_transform(
        self,
        points: np.ndarray,
        lower_bound: Optional[np.ndarray] = None,
        upper_bound: Optional[np.ndarray] = None,
    ) -> tuple[list[dict], list[SpatialAssignment]]:
        """
        Compute the spatial hierarchy from data using the spec's
        randomised subsampling algorithm.

        Parameters
        ----------
        points : (N, rank) array
        lower_bound, upper_bound : (rank,) arrays, optional
            Defaults derived from data if not provided.

        Returns
        -------
        metadata_ : list[dict]
            The "spatial" array for the info JSON.
        assignments : list[SpatialAssignment]
            Per-point level and cell where it was emitted.
        """
        points = np.asarray(points, dtype=np.float64)
        n, rank = points.shape

        self.lower_bound_ = (
            np.asarray(lower_bound, dtype=np.float64)
            if lower_bound is not None
            else points.min(axis=0)
        )
        self.upper_bound_ = (
            np.asarray(upper_bound, dtype=np.float64)
            if upper_bound is not None
            else points.max(axis=0) + 1e-6
        )

        self.grid_shapes_ = self._build_grid_shapes(rank)
        self._chunk_sizes = [self._compute_chunk_size(gs) for gs in self.grid_shapes_]

        rng = np.random.default_rng(self.seed)
        assignments: list[Optional[SpatialAssignment]] = [None] * n

        # Level-0 grouping
        all_idx = np.arange(n, dtype=int)
        remaining = self._group_by_cell(all_idx, self._cell_of(points, 0))

        self.metadata_ = []

        for level in range(len(self.grid_shapes_)):
            if not remaining:
                break

            max_count = max(len(v) for v in remaining.values())
            if max_count == 0:
                break

            prob = min(1.0, self.limit / max_count)
            leftovers: list[np.ndarray] = []

            for cell, pt_idx in remaining.items():
                mask = rng.random(len(pt_idx)) < prob
                for i in pt_idx[mask]:
                    assignments[i] = SpatialAssignment(level=level, cell=cell)
                kept = pt_idx[~mask]
                if len(kept):
                    leftovers.append(kept)

            self.metadata_.append(
                {
                    "key": f"spatial{level}",
                    "grid_shape": self.grid_shapes_[level].tolist(),
                    "chunk_size": self._chunk_sizes[level].tolist(),
                    "limit": self.limit,
                }
            )

            # Re-bin leftovers into next level's finer grid
            if level + 1 < len(self.grid_shapes_) and leftovers:
                leftover = np.concatenate(leftovers)
                remaining = self._group_by_cell(
                    leftover, self._cell_of(points[leftover], level + 1)
                )
            else:
                remaining = {}

        # Any stragglers that survived all levels get forced into the last
        # level at whatever cell they fall in.
        if remaining:
            last = len(self.metadata_) - 1
            for cell, pt_idx in remaining.items():
                for i in pt_idx:
                    assignments[i] = SpatialAssignment(level=last, cell=cell)

        self._fitted = True
        return assignments

    def fit(
        self,
        points: np.ndarray,
        lower_bound: Optional[np.ndarray] = None,
        upper_bound: Optional[np.ndarray] = None,
    ) -> list[dict]:
        """Convenience wrapper around fit_transform that discards assignments."""
        self.fit_transform(points, lower_bound, upper_bound)
        return self

    # ------------------------------------------------------------------
    # transform
    # ------------------------------------------------------------------

    def transform(
        self,
        points: np.ndarray,
        seed: Optional[int] = None,
    ) -> list[SpatialAssignment]:
        """
        Deterministically assign new points into the fitted hierarchy.

        Strategy: walk levels coarse-to-fine. At each level, for every
        cell, keep up to `limit` points (chosen by a seeded shuffle)
        and push the rest to the next finer level. Points still
        remaining after the last level are packed into their leaf cell.

        Parameters
        ----------
        points : (M, rank) array
        seed : int, optional
            RNG seed for the per-cell shuffle. Defaults to self.seed.

        Returns
        -------
        assignments : list[SpatialAssignment]
            Per-point level and cell.
        """
        self._ensure_fitted()

        points = np.asarray(points, dtype=np.float64)
        m = len(points)
        assignments: list[Optional[SpatialAssignment]] = [None] * m
        rng = np.random.default_rng(seed if seed is not None else self.seed)

        num_levels = len(self.metadata_)
        all_idx = np.arange(m, dtype=int)
        remaining = self._group_by_cell(all_idx, self._cell_of(points, 0))

        for level in range(num_levels):
            if not remaining:
                break

            leftovers: list[np.ndarray] = []

            for cell, pt_idx in remaining.items():
                if len(pt_idx) <= self.limit:
                    # Entire cell fits — emit everything here
                    for i in pt_idx:
                        assignments[i] = SpatialAssignment(level=level, cell=cell)
                else:
                    # Shuffle, take `limit`, push the rest down
                    rng.shuffle(pt_idx)
                    emitted, kept = pt_idx[: self.limit], pt_idx[self.limit :]
                    for i in emitted:
                        assignments[i] = SpatialAssignment(level=level, cell=cell)
                    leftovers.append(kept)

            # Re-bin leftovers into next level
            if level + 1 < num_levels and leftovers:
                leftover = np.concatenate(leftovers)
                remaining = self._group_by_cell(
                    leftover, self._cell_of(points[leftover], level + 1)
                )
            else:
                # Last level — force remaining into it
                if leftovers:
                    leftover = np.concatenate(leftovers)
                    coords = self._cell_of(points[leftover], level)
                    for i, coord in zip(leftover, coords):
                        assignments[i] = SpatialAssignment(
                            level=level, cell=tuple(coord)
                        )
                remaining = {}

        return assignments


n_samples = 100_000
sample_ratio = n_samples / len(synapses)
target_limit = 5000
input_limit = int(np.ceil(sample_ratio * target_limit))

sit = SpatialIndexTree(limit=input_limit, max_levels=20)
sit.fit(
    synapses.sample(n_samples)[
        ["ctr_pt_position_x", "ctr_pt_position_y", "ctr_pt_position_z"]
    ].values
)
assignments = sit.transform(
    synapses[["ctr_pt_position_x", "ctr_pt_position_y", "ctr_pt_position_z"]].values
)


# %%

from caveclient import CAVEclient

client = CAVEclient("minnie65_public")


back_state = 5624542562091008
state_info = client.state.get_state_json(back_state)

# %%
import neuroglancer

viewer = neuroglancer.Viewer()
viewer.set_state(state_info)


# %%
from webbrowser import open as open_browser

from neuroglancer.webdriver import Webdriver
from nglui.statebuilder import ViewerState
from PIL import Image

vs = ViewerState(base_state=state_info, interactive=True)
# # img = vs.viewer.screenshot(size=(400, 400))

# vs.to_neuroglancer_state()
# with vs._viewer.txn() as s:


headless = False
viewer = vs.viewer
with viewer.txn() as s:
    s.show_axis_lines = False
    s.layout.orthographic_projection = True
    s.show_scale_bar = True
    # s.projection_background_color = "#ffffff"

if headless:
    webdriver = Webdriver(viewer, headless=True, browser="chrome")
else:
    open_browser(viewer.get_viewer_url())
screenshot = viewer.screenshot(size=[2000, 1000], include_depth=False)
screenshot_image = Image(value=screenshot.screenshot.image)
