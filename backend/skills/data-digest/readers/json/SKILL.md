---
name: data-digest-json
description: "Sub-reader for JSON, JSONL, and NDJSON files. Handles nested objects, arrays of records, and large line-delimited streams with bounded analysis."
---

# JSON / JSONL Sub-Reader

## Applies To

`.json`, `.jsonl`, `.ndjson`, `.json.gz`, `.jsonl.gz`, `.ndjson.gz`

Use the probe result to determine:

- top-level type when known
- encoding
- whether counts are exact or estimated
- sample keys

Avoid shelling out to `jq` as a first step. The skill should work with standard Python plus optional dependencies that may already be available.

When the path ends with `.gz`, open it through `gzip` instead of plain `open(...)`.

---

## Operating Rules

1. For plain JSON, distinguish object vs array before choosing a strategy.
2. For JSONL/NDJSON, never load the whole file when it is large.
3. For nested records, flatten only as far as needed to answer the question.
4. When keys come from a bounded sample instead of a full scan, say that the schema is partial.
5. Do not recommend package installation unless the task truly requires it.

---

## Small JSON Files

Use a full parse when the file is comfortably small:

```python
import gzip
import json
from pathlib import Path

import pandas as pd

filepath = "/absolute/path/to/file.json"


def open_text_input(filepath: str, *, encoding: str = "utf-8"):
    path = Path(filepath)
    if path.suffix.lower() == ".gz":
        return gzip.open(path, "rt", encoding=encoding, errors="replace")
    return path.open("r", encoding=encoding, errors="replace")


with open_text_input(filepath) as handle:
    data = json.load(handle)

if isinstance(data, list):
    print(f"Top-level array length: {len(data)}")
    df = pd.json_normalize(data, max_level=1)
    print(f"Shape: {df.shape}")
    print(f"Columns: {list(df.columns)}")
    print(df.head(5).to_string())
elif isinstance(data, dict):
    print(f"Top-level object keys: {list(data.keys())[:20]}")
    preview_keys = list(data.keys())[:10]
    preview = {key: data[key] for key in preview_keys}
    print(json.dumps(preview, indent=2, ensure_ascii=False, default=str))
else:
    print({"type": type(data).__name__, "value": data})
```

---

## Large JSON Arrays

If `ijson` is already available, stream records. If not, do bounded structure inspection and answer cautiously.

```python
import gzip
import json
from pathlib import Path

filepath = "/absolute/path/to/file.json"


def open_text_input(filepath: str, *, encoding: str = "utf-8"):
    path = Path(filepath)
    if path.suffix.lower() == ".gz":
        return gzip.open(path, "rt", encoding=encoding, errors="replace")
    return path.open("r", encoding=encoding, errors="replace")


def open_binary_input(filepath: str):
    path = Path(filepath)
    if path.suffix.lower() == ".gz":
        return gzip.open(path, "rb")
    return path.open("rb")


try:
    import ijson
except ImportError:
    ijson = None

if ijson is None:
    with open_text_input(filepath) as handle:
        sample = handle.read(100_000)
    print("Streaming parser not available; using bounded preview only.")
    print(sample[:3000])
else:
    samples = []
    record_count = 0
    with open_binary_input(filepath) as handle:
        for record in ijson.items(handle, "item"):
            record_count += 1
            if len(samples) < 20:
                samples.append(record)
            if record_count >= 50_000:
                break

    print(f"Records scanned: {record_count}")
    if samples:
        print(f"Sample keys: {list(samples[0].keys()) if isinstance(samples[0], dict) else type(samples[0]).__name__}")
```

If you had to use the bounded-preview fallback, say explicitly that you did not fully parse the array.

---

## JSONL / NDJSON

Use chunked reads with bounded categorical tracking:

```python
from collections import Counter
import gzip
import json
from pathlib import Path

import pandas as pd

filepath = "/absolute/path/to/file.jsonl"
CHUNK_SIZE = 2000
TOP_K = 10
MAX_TRACKED_VALUES = 100


def open_text_input(filepath: str, *, encoding: str = "utf-8"):
    path = Path(filepath)
    if path.suffix.lower() == ".gz":
        return gzip.open(path, "rt", encoding=encoding, errors="replace")
    return path.open("r", encoding=encoding, errors="replace")


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
        "notes": ["Field type drifted across chunks; summarize it as mixed."],
    }
    stats[column] = mixed_bucket
    return mixed_bucket


def update_running_stats(stats: dict, df: pd.DataFrame) -> None:
    for col in df.columns:
        numeric_view = pd.to_numeric(df[col], errors="coerce")
        if numeric_view.notna().mean() >= 0.8 and numeric_view.notna().any():
            bucket = ensure_bucket(stats, col, "numeric")
            if bucket["kind"] == "numeric":
                bucket["min"] = min(bucket["min"], float(numeric_view.min()))
                bucket["max"] = max(bucket["max"], float(numeric_view.max()))
                bucket["sum"] += float(numeric_view.sum())
                bucket["count"] += int(numeric_view.count())
                bucket["nulls"] += int(df[col].isna().sum())
            else:
                bucket["nulls"] += int(df[col].isna().sum())
                bucket["top_values"].update(df[col].fillna("<NULL>").astype(str).value_counts().head(TOP_K * 3).to_dict())
                trim_counter(bucket["top_values"], MAX_TRACKED_VALUES)
            continue

        sample_cardinality = df[col].nunique(dropna=False)
        if sample_cardinality <= 200:
            bucket = ensure_bucket(stats, col, "categorical")
            bucket["nulls"] += int(df[col].isna().sum())
            bucket["top_values"].update(df[col].fillna("<NULL>").astype(str).value_counts().head(TOP_K * 3).to_dict())
            trim_counter(bucket["top_values"], MAX_TRACKED_VALUES)


running_stats = {}
records = []
rows_scanned = 0

with open_text_input(filepath) as handle:
    for line in handle:
        line = line.strip()
        if not line:
            continue
        rows_scanned += 1
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

        if len(records) >= CHUNK_SIZE:
            update_running_stats(running_stats, pd.json_normalize(records, max_level=2))
            records = []

if records:
    update_running_stats(running_stats, pd.json_normalize(records, max_level=2))

print(f"Rows analyzed: {rows_scanned}")
for col, bucket in running_stats.items():
    if bucket["kind"] == "numeric":
        mean = bucket["sum"] / bucket["count"] if bucket["count"] else 0.0
        print(f"{col}: min={bucket['min']}, max={bucket['max']}, mean={mean:.2f}, nulls={bucket['nulls']}")
    else:
        label = "mixed" if bucket["kind"] == "mixed" else "categorical"
        print(f"{col} ({label}): top={dict(bucket['top_values'].most_common(TOP_K))}, nulls={bucket['nulls']}")
        for note in bucket.get("notes", []):
            print(f"  Note: {note}")
```

If the file was too large to scan fully, present the discovered keys as a sampled schema hint, not a complete schema contract.

---

## Schema Discovery

Use this when the user asks for structure rather than findings:

```python
import gzip
import json
from pathlib import Path

filepath = "/absolute/path/to/file.json"
probe_size_category = "small"  # Replace with probe_report["size_category"]


def discover_schema(value, depth=0, max_depth=3):
    if depth >= max_depth:
        return {"type": type(value).__name__}
    if isinstance(value, dict):
        return {
            "type": "object",
            "keys": {key: discover_schema(item, depth + 1, max_depth) for key, item in list(value.items())[:20]},
        }
    if isinstance(value, list):
        sample = value[0] if value else None
        return {
            "type": "array",
            "length": len(value),
            "item_schema": discover_schema(sample, depth + 1, max_depth) if sample is not None else None,
        }
    return {"type": type(value).__name__, "sample": repr(value)[:80]}



def open_text_input(filepath: str, *, encoding: str = "utf-8"):
    path = Path(filepath)
    if path.suffix.lower() == ".gz":
        return gzip.open(path, "rt", encoding=encoding, errors="replace")
    return path.open("r", encoding=encoding, errors="replace")


if probe_size_category not in {"small", "medium"}:
    print("Large JSON: use the probe preview or a streamed sample instead of full json.load() for schema discovery.")
else:
    with open_text_input(filepath) as handle:
        data = json.load(handle)
    print(json.dumps(discover_schema(data), indent=2, ensure_ascii=False))
```

---

## Edge Cases

- Mixed-type arrays: say so explicitly instead of flattening them aggressively.
- Deep nesting: flatten only to the level needed for the user's question.
- Invalid JSONL rows: count or mention skipped rows when they matter.
- Duplicate keys in JSON objects: Python keeps the last one; warn if the file appears to rely on duplicates.

When the JSON is both huge and structurally messy, give a structure summary first and push for a narrower follow-up question.
