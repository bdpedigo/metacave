# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "caveclient[cv]>=8.0.1",
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
from nglui.precomputed import AnnotationDataFrameWriter
from nglui.statebuilder import ViewerState

# %% LOAD SYNAPSE TABLES

print("Loading synapse table...")
table_path = Path("/Users/ben.pedigo/code/meshrep/meshrep/data/mega_tables")
synapses_pl = pl.read_parquet(table_path / "filtered_synapses.parquet")
synapses = synapses_pl.to_pandas()
root_ids = [
    864691135489514810,
    864691135495542672,
    864691136991202453,
    864691136662432990,
    864691136311213914,
]

synapses = synapses.query("post_pt_root_id.isin(@root_ids)")
print("Done loading synapse table.")

# %%

print("Initializing CAVE client...")
client = CAVEclient("minnie65_phase3_v1")
print("Done initializing CAVE client.")

print("Writing precomputed data...")
writer = AnnotationDataFrameWriter(
    segmentation_source=client,
    point_column=["ctr_pt_position_x", "ctr_pt_position_y", "ctr_pt_position_z"],
    property_columns=["size"],
    relationship_columns=["post_pt_root_id"],
    id_column="synapse_id",
    data_resolution=[4, 4, 40],
    write_sharded=True,
)

out_path = "/Users/ben.pedigo/code/meshrep/meshrep/data/test_local_precomputed"
out_path = "gs://allen-minnie-phase3/bdp-synapse-mega-tables/test-precomputed-write"

# clear out_path if it exists
if not out_path.startswith("gs://") and Path(out_path).exists():
    import shutil

    shutil.rmtree(out_path)

writer.write(synapses, out_path)
print("Done writing precomputed data.")

# %%


if out_path.startswith("gs://"):
    vs = (
        ViewerState(client=client)
        .add_layers_from_client()
        .add_annotation_source(
            f"precomputed://{out_path}",
            relationship_columns=["post_pt_root_id"],
        )
    )
    vs.to_browser()

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
            relationship_columns=["post_pt_root_id"],
        )
    )
    vs.to_browser()

    try:
        print("Server running. Press Ctrl+C to quit.")
        threading.Event().wait()
    except KeyboardInterrupt:
        httpd.shutdown()
        print("Server stopped.")
