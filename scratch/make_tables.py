# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "caveclient>=8.0.1",
#     "ipykernel>=7.2.0",
#     "ipywidgets>=8.1.8",
#     "polars>=1.39.3",
#     "pyarrow>=23.0.1",
# ]
#
# [tool.uv.sources]
# caveclient = { path = "../submodules/caveclient", editable = true }
# ///
# %%



from caveclient import CAVEclient

client = CAVEclient("minnie65_phase3_v1", version=1412)

client.materialize.get_tables()

#%%
dir(client.auth)
client.auth.request_header
#%%
dir(client.catalog)

client.catalog._default_url_mapping["catalog_server_address"] = "http://127.0.0.1:8000"
client.catalog._server_address = "http://127.0.0.1:8000"
print(client.catalog.api_version)
print(client.catalog.list_assets())
# %%

client.catalog.register_asset(
    name="aibs_cell_info",
    uri="gs://mat_dbs/test/aibs_cell_info2.parquet",
    format="parquet",
    asset_type="table",
    is_managed=False,
    mat_version=1412,
    revision=6,
    mutability="static",
    maturity="stable",
)

# %%
client.catalog.list_assets()

# %%
table = client.materialize.query_view("aibs_cell_info")

# %%
table.to_csv("aibs_cell_info.csv")

# %%

# csv_path = "https://storage.googleapis.com/mat_dbs/public/minnie65_phase3_v1/v1412/aibs_cell_info.csv.gz"
# header_path = "https://storage.googleapis.com/mat_dbs/public/minnie65_phase3_v1/v1412/aibs_cell_info_header.csv"


# SQL_TO_POLARS_DTYPE = {
#     "bigint": pl.Int64,
#     "integer": pl.Int32,
#     "smallint": pl.Int16,
#     "real": pl.Float32,
#     "double precision": pl.Float64,
#     "numeric": pl.Decimal,
#     "boolean": pl.Boolean,
#     "text": pl.String,
#     "varchar": pl.String,
#     "character varying": pl.String,
#     "date": pl.Date,
#     "timestamp without time zone": pl.Datetime,
#     "timestamp with time zone": pl.Datetime,
#     "user-defined": pl.String,  # fallback for unrecognized types
# }


# def sql_to_polars_dtype(sql_type: str) -> pl.datatypes.DataType:
#     """
#     Convert a SQL dtype string to a Polars dtype.
#     Raises ValueError if the dtype is not recognized.
#     """
#     sql_type = sql_type.strip().lower()
#     # handle e.g. 'character varying(255)'
#     if "(" in sql_type:
#         sql_type = sql_type.split("(")[0].strip()
#     if sql_type not in SQL_TO_POLARS_DTYPE:
#         valid = ", ".join(sorted(SQL_TO_POLARS_DTYPE))
#         raise ValueError(
#             f"Unrecognized SQL dtype: {sql_type!r}. Valid options: {valid}"
#         )
#     return SQL_TO_POLARS_DTYPE[sql_type]


# def build_polars_schema(schema_df):
#     """
#     Given a DataFrame with columns ['field', 'dtype'],
#     return a dict usable as a Polars schema.
#     """
#     return {
#         row["column_name"]: sql_to_polars_dtype(row["data_type"])
#         for row in schema_df.iter_rows(named=True)
#     }


# header = pl.read_csv(
#     header_path, has_header=False, new_columns=["column_name", "data_type"]
# )


# schema = build_polars_schema(header)


# table = pl.scan_csv(csv_path, has_header=False, schema=schema)
# table.collect()
