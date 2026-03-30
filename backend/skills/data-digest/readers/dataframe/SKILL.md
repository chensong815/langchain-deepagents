---
name: data-digest-dataframe
description: "Sub-reader for CSV, TSV, and other flat tabular files. Uses bounded previews for small files and chunked, memory-safe summaries for large files."
---

# DataFrame Sub-Reader

## Applies To

`.csv`, `.tsv`, `.csv.gz`, `.tsv.gz`, and other flat tables that pandas can read safely.

Use the probe output first. In particular, carry over:

- `filepath`
- `encoding`
- `separator` if the probe determined one
- `size_category`
- `columns`

---

## Operating Rules

1. Do not hardcode a fake upload path. Use the real `filepath`.
2. For `large` and `huge` files, do not accumulate every distinct categorical value.
3. Keep categorical summaries bounded with top-k counters.
4. Prefer targeted columns when the user asks a narrow question.
5. Trust logical `row_count` from the probe over raw `line_count` when quoted multiline fields are possible.

---

## Small / Medium Files

Use a direct load when the file is comfortably sized:

```python
import pandas as pd

filepath = "/absolute/path/to/file.csv"
sep = ","
encoding = "utf-8"

df = pd.read_csv(
    filepath,
    sep=sep,
    encoding=encoding,
    compression="infer",
    on_bad_lines="skip",
    low_memory=False,
)

print(f"Shape: {df.shape}")
print(f"Columns: {list(df.columns)}")
print("\nNull counts:")
print(df.isnull().sum())

num_cols = df.select_dtypes(include="number").columns.tolist()
if num_cols:
    print("\nNumeric summary:")
    print(df[num_cols].describe().transpose())

cat_cols = df.select_dtypes(include=["object", "string", "category"]).columns.tolist()
for col in cat_cols[:10]:
    counts = df[col].fillna("<NULL>").astype(str).value_counts().head(10)
    print(f"\nTop values for {col}:")
    print(counts.to_string())
```

---

## Large / Huge Files

Use chunked analysis with bounded statistics:

```python
from collections import Counter

import pandas as pd

filepath = "/absolute/path/to/file.csv"
sep = ","
encoding = "utf-8"
CHUNK_SIZE = 2000
TOP_K = 10
MAX_TRACKED_VALUES = 100


def trim_counter(counter: Counter, limit: int) -> None:
    for key, _ in counter.most_common()[limit:]:
        del counter[key]


def ensure_bucket(stats: dict, column: str, kind: str) -> dict:
    bucket = stats.get(column)
    if bucket is None:
        if kind == "numeric":
            bucket = {"kind": "numeric", "min": float("inf"), "max": float("-inf"), "sum": 0.0, "count": 0, "nulls": 0}
        else:
            bucket = {"kind": "categorical", "nulls": 0, "top_values": Counter()}
        stats[column] = bucket
        return bucket

    if bucket["kind"] == kind:
        return bucket

    mixed_bucket = {
        "kind": "mixed",
        "nulls": bucket.get("nulls", 0),
        "top_values": bucket.get("top_values", Counter()),
        "notes": ["Column type drifted across chunks; summarize as mixed instead of numeric-only."],
    }
    stats[column] = mixed_bucket
    return mixed_bucket


def update_numeric(bucket: dict, series: pd.Series) -> None:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        bucket["nulls"] += int(series.isna().sum())
        return

    bucket["min"] = min(bucket["min"], float(values.min()))
    bucket["max"] = max(bucket["max"], float(values.max()))
    bucket["sum"] += float(values.sum())
    bucket["count"] += int(values.count())
    bucket["nulls"] += int(series.isna().sum())


def update_categorical(bucket: dict, series: pd.Series) -> None:
    bucket["nulls"] += int(series.isna().sum())
    sample_counts = series.fillna("<NULL>").astype(str).value_counts().head(TOP_K * 3)
    bucket["top_values"].update({key: int(value) for key, value in sample_counts.items()})
    trim_counter(bucket["top_values"], MAX_TRACKED_VALUES)


column_stats = {}
total_rows = 0
bad_chunks = 0

for chunk in pd.read_csv(
    filepath,
    sep=sep,
    encoding=encoding,
    compression="infer",
    chunksize=CHUNK_SIZE,
    on_bad_lines="skip",
    low_memory=False,
):
    total_rows += len(chunk)

    for col in chunk.columns:
        numeric_view = pd.to_numeric(chunk[col], errors="coerce")
        numeric_ratio = numeric_view.notna().mean()
        if numeric_ratio >= 0.8:
            bucket = ensure_bucket(column_stats, col, "numeric")
            if bucket["kind"] == "numeric":
                update_numeric(bucket, chunk[col])
            else:
                update_categorical(bucket, chunk[col].astype(str))
            continue

        unique_sample = chunk[col].nunique(dropna=False)
        if unique_sample <= 200:
            bucket = ensure_bucket(column_stats, col, "categorical")
            update_categorical(bucket, chunk[col])
        else:
            bad_chunks += 1

print(f"Rows analyzed: {total_rows}")

for col, bucket in column_stats.items():
    if bucket["kind"] == "numeric":
        mean = bucket["sum"] / bucket["count"] if bucket["count"] else 0.0
        print(f"\n{col} (numeric)")
        print(f"  Range: {bucket['min']} -> {bucket['max']}")
        print(f"  Mean: {mean:.2f}")
        print(f"  Nulls: {bucket['nulls']}")
        continue

    label = "mixed" if bucket["kind"] == "mixed" else "categorical"
    print(f"\n{col} ({label})")
    print(f"  Nulls: {bucket['nulls']}")
    print(f"  Top values: {dict(bucket['top_values'].most_common(TOP_K))}")
    for note in bucket.get("notes", []):
        print(f"  Note: {note}")

if bad_chunks:
    print(f"\nSkipped high-cardinality categorical tracking in {bad_chunks} chunk scans.")
```

This pattern is deliberately approximate for large files. That is acceptable as long as you say so in the answer.

---

## Targeted Analysis

If the user asks a focused question, read only the relevant columns:

```python
import pandas as pd

filepath = "/absolute/path/to/file.csv"
cols = ["date", "revenue", "category"]

for chunk in pd.read_csv(filepath, usecols=cols, chunksize=5000, compression="infer"):
    filtered = chunk[chunk["category"] == "Electronics"]
    if not filtered.empty:
        print(filtered.describe(include="all"))
```

---

## Common Pitfalls

- Mixed types: use `pd.to_numeric(..., errors="coerce")` instead of assuming a column is clean.
- Giant fields: if the probe warned about a very wide column, keep previews clipped and avoid printing full cell values.
- Bad rows: keep `on_bad_lines="skip"` unless the user asked for row-level forensics.
- Wide tables: reduce `CHUNK_SIZE` when there are dozens of columns.
- Header issues: if the probe suggests a strange first row, reload with `header=None` or a different `skiprows`.

When the table looks more like an event log than a dataset, switch to the text reader instead of forcing tabular assumptions.
