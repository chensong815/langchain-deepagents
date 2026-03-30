---
name: data-digest-txt
description: "Sub-reader for logs, plain text, Markdown, and config-like text files. Focuses on pattern detection, bounded previews, and log aggregation."
---

# Text / Log Sub-Reader

## Applies To

`.txt`, `.log`, `.md`, `.text`, `.yaml`, `.yml`, `.ini`, `.conf`, `.toml`, plus gzip-compressed variants such as `.log.gz` and `.txt.gz`

This reader is for human-readable text and configuration-like files. It is not the general source-code analysis workflow.

---

## Operating Rules

1. Determine the text pattern before reading deeply.
2. Prefer line-bounded reads over `read()` on large files.
3. For logs, aggregate by severity and time instead of summarizing raw prose.
4. For config-like files, extract sections and key-value pairs instead of paraphrasing the whole file.

---

## Step 1: Detect the Pattern

```python
import gzip
import re
from pathlib import Path

filepath = "/absolute/path/to/file.log"


def open_text_input(filepath: str, *, encoding: str = "utf-8"):
    path = Path(filepath)
    if path.suffix.lower() == ".gz":
        return gzip.open(path, "rt", encoding=encoding, errors="replace")
    return path.open("r", encoding=encoding, errors="replace")


with open_text_input(filepath) as handle:
    sample = handle.read(5000)

is_log = sum(
    bool(re.search(pattern, sample))
    for pattern in (
        r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}",
        r"\b(INFO|WARN|WARNING|ERROR|DEBUG|FATAL|TRACE)\b",
        r"\[[A-Z]+\]",
    )
) >= 2

is_config = sum(
    token in sample.lower()
    for token in ("[section", "=", ":", "export ", "{")
) >= 3

pattern = "log" if is_log else "config" if is_config else "prose"
print(f"Detected pattern: {pattern}")
```

---

## Structured Logs

Use aggregation, not raw full-text summarization:

```python
import gzip
import re
from collections import Counter
from pathlib import Path

filepath = "/absolute/path/to/file.log"
timestamp_pattern = r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})"
level_pattern = r"\b(INFO|WARN|WARNING|ERROR|DEBUG|FATAL|TRACE)\b"


def open_text_input(filepath: str, *, encoding: str = "utf-8"):
    path = Path(filepath)
    if path.suffix.lower() == ".gz":
        return gzip.open(path, "rt", encoding=encoding, errors="replace")
    return path.open("r", encoding=encoding, errors="replace")

levels = Counter()
first_timestamp = None
last_timestamp = None
errors = []

with open_text_input(filepath) as handle:
    for line_number, line in enumerate(handle, start=1):
        if match := re.search(level_pattern, line):
            level = match.group(1)
            levels[level] += 1
            if level in {"ERROR", "FATAL"} and len(errors) < 20:
                errors.append((line_number, line.strip()[:240]))

        if ts_match := re.search(timestamp_pattern, line):
            if first_timestamp is None:
                first_timestamp = ts_match.group(1)
            last_timestamp = ts_match.group(1)

print(f"Time range: {first_timestamp} -> {last_timestamp}")
print(f"Level counts: {dict(levels)}")
print(f"Sample errors: {errors}")
```

---

## Plain Text / Markdown

Use bounded chunk previews and capture the sentences with numbers:

```python
import gzip
import re
from pathlib import Path

filepath = "/absolute/path/to/file.txt"
CHUNK_LINES = 200


def open_text_input(filepath: str, *, encoding: str = "utf-8"):
    path = Path(filepath)
    if path.suffix.lower() == ".gz":
        return gzip.open(path, "rt", encoding=encoding, errors="replace")
    return path.open("r", encoding=encoding, errors="replace")

with open_text_input(filepath) as handle:
    for chunk_id in range(5):
        lines = []
        for _ in range(CHUNK_LINES):
            line = handle.readline()
            if not line:
                break
            lines.append(line)
        if not lines:
            break

        text = "".join(lines)
        sentences = re.split(r"[.!?]\s+", text)
        number_sentences = [item.strip() for item in sentences if re.search(r"\d", item)]
        print(
            {
                "chunk": chunk_id,
                "line_count": len(lines),
                "preview": text[:400],
                "number_sentences": number_sentences[:5],
            }
        )
```

If the file is much larger than the first few chunks, say that the answer is based on sampled sections unless you continue scanning.

---

## Config-Like Files

Prefer extracting concrete keys and sections:

```python
import gzip
from collections import defaultdict
from pathlib import Path

filepath = "/absolute/path/to/file.conf"
sections = defaultdict(list)
current_section = "root"


def open_text_input(filepath: str, *, encoding: str = "utf-8"):
    path = Path(filepath)
    if path.suffix.lower() == ".gz":
        return gzip.open(path, "rt", encoding=encoding, errors="replace")
    return path.open("r", encoding=encoding, errors="replace")

with open_text_input(filepath) as handle:
    for raw_line in handle:
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1].strip() or "root"
            continue
        sections[current_section].append(line)

for section, items in sections.items():
    print({"section": section, "items": items[:20]})
```

---

## Safe Reading Helper

Use this when the encoding is uncertain:

```python
def safe_read(filepath: str, max_chars: int | None = None) -> str:
    import gzip
    import chardet
    from pathlib import Path

    path = Path(filepath)
    if path.suffix.lower() == ".gz":
        with gzip.open(path, "rb") as handle:
            sample = handle.read(20_000)
    else:
        with path.open("rb") as handle:
            sample = handle.read(20_000)

    detected = chardet.detect(sample)
    encoding = detected.get("encoding") or "utf-8"

    if path.suffix.lower() == ".gz":
        with gzip.open(path, "rt", encoding=encoding, errors="replace") as handle:
            text = handle.read(max_chars) if max_chars else handle.read()
    else:
        with path.open("r", encoding=encoding, errors="replace") as handle:
            text = handle.read(max_chars) if max_chars else handle.read()

    return text
```

---

## Edge Cases

- Binary disguised as text: use `file` before forcing a read.
- Very long lines: read line-by-line instead of slurping the whole file.
- BOM at the start: try `utf-8-sig` if the first key or first line looks corrupted.
- Mixed content: split logs, prose, and config sections before summarizing.

When the file behaves more like structured events than prose, answer from counts, timestamps, and repeated patterns rather than from narrative summary.
