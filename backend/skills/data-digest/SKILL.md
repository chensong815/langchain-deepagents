---
name: data-digest
description: "Use this skill when the user wants to inspect, summarize, or answer questions about a data file that may be too large to read directly. Best for CSV/TSV, Excel workbooks, JSON/JSONL, logs, and plain-text exports."
triggers:
  - summarize this file
  - analyze this dataset
  - what's in this file
  - large csv
  - jsonl
  - excel workbook
  - log file
required-slots:
  - filepath
output-contract: "Return a concise digest that states file scope, analysis coverage, key findings with numbers, data quality notes, and recommended follow-up questions."
---

# Data Digest

Read and summarize large data files without blindly loading everything into context.

Keep the frontmatter aligned with the body: `required-slots` should stay minimal and real, and `output-contract` should match the final answer shape this skill asks for.

## Inputs You Need

- `filepath`: the real absolute file path from the current session or artifact list
- `user_question`: what the user actually wants to know
- `probe_report`: output from `scripts/probe.py`

Never invent a path such as `/mnt/user-data/uploads/FILENAME`. Always use the actual file path available in this session.

---

## Step 1: Probe First

Run the probe before deeper analysis:

```bash
python3 <skill_dir>/scripts/probe.py "/absolute/path/to/file"
```

The probe is intentionally lightweight. It should give you:

- file type and extension
- encoding
- row or line counts when cheap to obtain, or an estimate when not
- logical CSV/TSV row counts when exact record parsing is cheap enough
- head preview and limited structure hints
- size category
- recommended sub-reader
- warnings about uncertainty or missing dependencies

For gzip-compressed text-style data files such as `.csv.gz`, `.json.gz`, `.jsonl.gz`, and `.txt.gz`, the probe reads the decompressed stream transparently.

If the probe fails:

1. Read the error message.
2. Run `file "/absolute/path/to/file"` for a second opinion.
3. Continue with a bounded manual inspection instead of abandoning the task.

---

## Step 2: Pick Exactly One Reader

Prefer `probe_report["sub_reader"]` when present.

| File type | Reader |
|---|---|
| `.csv`, `.tsv` | `readers/dataframe/SKILL.md` |
| `.csv.gz`, `.tsv.gz` | `readers/dataframe/SKILL.md` |
| `.xlsx`, `.xlsm`, `.xls`, `.ods` | `readers/excel/SKILL.md` |
| `.json`, `.jsonl`, `.ndjson` | `readers/json/SKILL.md` |
| `.json.gz`, `.jsonl.gz`, `.ndjson.gz` | `readers/json/SKILL.md` |
| `.txt`, `.log`, `.md`, `.text`, `.yaml`, `.yml`, `.ini`, `.conf`, `.toml` | `readers/txt/SKILL.md` |
| `.txt.gz`, `.log.gz`, `.md.gz` and similar gzip-compressed text files | `readers/txt/SKILL.md` |

Do not read every sub-reader. Read only the one that matches the file you are handling.

If the extension is unknown:

1. Use the probe output plus `file`.
2. Pick the closest reader.
3. If nothing fits, write a small custom script and keep the read bounded.

---

## Step 3: Analyze for the User's Question

The user's question decides the depth:

- If they ask for a general summary, produce a digest.
- If they ask a narrow question, answer that first and summarize only what is relevant.
- If the file is huge, prefer chunking, sampling, or targeted columns/keys over full reads.

---

## Universal Rules

1. Use the real `filepath` and the interpreter that exists in the environment. In this repo, `python3` is the safe default.
2. Keep the probe cheap. Heavy analysis belongs in the reader step, not in `probe.py`.
3. For `large` and `huge` files, use bounded reads only.
4. Preserve numbers. Say `2.1M -> 3.4M (+62%)`, not just "increased".
5. State coverage explicitly when you did not inspect the whole file.
6. If the probe says columns or schema came from a bounded sample, say that explicitly instead of presenting them as complete.
7. Treat warnings from the probe as first-class signals, not noise.
8. Do not use this skill for source-code comprehension; use the normal code-reading workflow for that.

---

## Size Categories

| Category | Typical meaning | Default strategy |
|---|---|---|
| `small` | Easy to load directly | Full read |
| `medium` | Still manageable in memory | Full read plus column/key summary |
| `large` | Too big for blind full reads | Chunked summary |
| `huge` | Expensive even for chunking | Query-driven, sampled, or targeted analysis |

---

## Output Shape

Use this structure for the final answer unless the user asked for something narrower:

```md
## Data Digest: <filename>

**Overview**
- File type, size, and shape
- What portion was analyzed
- Key fields, sheets, or sections

**Key Findings**
1. Specific finding with numbers
2. Specific finding with numbers
3. Specific finding with numbers

**Data Quality**
- Missing values, malformed rows, duplicates, schema drift, or suspicious sections

**Next Useful Questions**
- Suggested follow-up analyses
```

---

## When to Stop and Narrow

Do not keep drilling indefinitely. Stop and narrow the task when:

- the file is huge and the user has no concrete question
- the schema is highly nested or inconsistent
- optional dependencies are missing and a full parser is not available
- the cheapest safe answer is a structure summary plus a follow-up question

When that happens, say exactly what you inspected and what remains unexamined.
