"""CSV / JSON row ingestion with the shared clean_inputs rules."""
import io

import pandas as pd

from features import RAW_COLS, clean_inputs, normalize_columns

MAX_BYTES = 1 * 1024 * 1024
MAX_ROWS = int(__import__("os").getenv("MAX_ROWS", "1000"))


def rows_to_df(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError("Provide a non-empty list of row objects.")
    if len(rows) > MAX_ROWS:
        raise ValueError("Too many rows: %d (max %d)." % (len(rows), MAX_ROWS))
    df = pd.DataFrame(rows)
    return normalize_columns(df)


def parse_csv_bytes(data, filename="upload.csv"):
    if data is None or len(data) == 0:
        raise ValueError("Empty file.")
    if len(data) > MAX_BYTES:
        raise ValueError("File too large (max 1 MB).")
    name = (filename or "upload.csv").lower()
    if not name.endswith(".csv"):
        raise ValueError("Only .csv files are accepted.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    df = pd.read_csv(io.StringIO(text))
    if len(df) > MAX_ROWS:
        raise ValueError("Too many rows: %d (max %d)." % (len(df), MAX_ROWS))
    return normalize_columns(df)


def clean_for_api(df, drop_duplicates=False):
    miss = [c for c in RAW_COLS if c not in df.columns]
    if miss:
        raise ValueError("Missing columns: %s. Expected: %s" % (miss, RAW_COLS))
    return clean_inputs(df, drop_duplicate_keep_last=drop_duplicates)
