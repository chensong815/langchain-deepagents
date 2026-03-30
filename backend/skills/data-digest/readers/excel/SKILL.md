---
name: data-digest-excel
description: "Sub-reader for Excel workbooks. Starts from sheet-level structure and uses streaming reads for larger sheets."
---

# Excel Sub-Reader

## Applies To

`.xlsx`, `.xlsm`, `.xls`, `.ods`

Always start with sheet structure from the probe. For workbook files, the first useful question is often "which sheet matters?"

---

## Operating Rules

1. Summarize the workbook before diving into a single sheet.
2. Prefer sheet-by-sheet analysis.
3. For larger workbooks, stream rows instead of converting every sheet to a DataFrame at once.
4. If formulas matter, mention whether you are reading cached values or formulas.

---

## Workbook Overview

Use this first when the user asks for a general Excel summary:

```python
from openpyxl import load_workbook

filepath = "/absolute/path/to/workbook.xlsx"
wb = load_workbook(filepath, read_only=True, data_only=True)

for sheet_name in wb.sheetnames:
    ws = wb[sheet_name]
    print(
        {
            "sheet": sheet_name,
            "rows": ws.max_row,
            "columns": ws.max_column,
            "state": ws.sheet_state,
        }
    )

wb.close()
```

If there are many sheets, summarize all sheets briefly and then focus on the sheet most relevant to the user's question.

---

## Small / Medium Sheets

For a single manageable sheet, direct pandas loading is fine:

```python
import pandas as pd

filepath = "/absolute/path/to/workbook.xlsx"
sheet_name = "Sheet1"

df = pd.read_excel(filepath, sheet_name=sheet_name)
print(f"Shape: {df.shape}")
print(f"Columns: {list(df.columns)}")
print("\nNull counts:")
print(df.isnull().sum())

num_cols = df.select_dtypes(include="number").columns.tolist()
if num_cols:
    print("\nNumeric summary:")
    print(df[num_cols].describe().transpose())
```

---

## Large Sheets

Use streaming rows and summarize chunks. This version defines the helper before use, so it is safe to copy:

```python
from collections import Counter

import pandas as pd
from openpyxl import load_workbook

filepath = "/absolute/path/to/workbook.xlsx"
sheet_name = "Sheet1"
CHUNK_SIZE = 2000


def summarize_chunk(header: list[str], rows: list[tuple]) -> dict:
    df = pd.DataFrame(rows, columns=header)
    summary = {"rows": len(df), "numeric": {}, "categorical": {}}

    for col in df.columns:
        numeric_view = pd.to_numeric(df[col], errors="coerce")
        if numeric_view.notna().mean() >= 0.8 and numeric_view.notna().any():
            summary["numeric"][col] = {
                "min": float(numeric_view.min()),
                "max": float(numeric_view.max()),
                "mean": float(numeric_view.mean()),
            }
            continue

        counts = Counter(df[col].fillna("<NULL>").astype(str).value_counts().head(5).to_dict())
        if counts:
            summary["categorical"][col] = dict(counts)

    return summary


wb = load_workbook(filepath, read_only=True, data_only=True)
ws = wb[sheet_name]

header = []
rows = []
chunk_id = 0

for row_index, row in enumerate(ws.iter_rows(values_only=True)):
    values = ["" if value is None else value for value in row]
    if row_index == 0:
        header = [str(value) if value != "" else f"col_{idx}" for idx, value in enumerate(values)]
        continue

    rows.append(tuple(values))
    if len(rows) >= CHUNK_SIZE:
        print({"chunk": chunk_id, **summarize_chunk(header, rows)})
        rows = []
        chunk_id += 1

if rows:
    print({"chunk": chunk_id, **summarize_chunk(header, rows)})

wb.close()
```

---

## Legacy `.xls` and `.ods`

- `.xls`: use `xlrd`-based readers only if the environment already has that dependency.
- `.ods`: prefer lightweight sheet inspection first, then load only the target sheet.
- If the lightweight probe could not produce row counts, say so instead of pretending the workbook size is known.

---

## Important Edge Cases

- Merged cells: `read_only=True` is fast but gives limited merge metadata.
- Formula cells: `data_only=True` returns cached values and may yield `None` when the workbook was not recalculated.
- Hidden sheets: include them in the workbook overview because they sometimes contain reference tables or control data.
- Header drift: if the first visible row is metadata instead of a true header, reload with `header=None` or `skiprows`.

When the workbook is huge and the user has no target sheet, stop after a workbook overview and ask which sheet or metric matters most.
