"""工具集合：供 deep agent 在推理中调用。"""

from __future__ import annotations

import csv
import importlib
import io
import json
import os
import re
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, request
from uuid import uuid4

from langchain.tools import tool
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from app.config import get_settings
from app.serialization import make_json_safe
from app.sandbox import get_current_session_sandbox


FIELD_LINEAGE_STOP_MESSAGE = "目标字段相关血缘已完成查询"
DEFAULT_FIELD_LINEAGE_MAX_ROUNDS = 20
DEFAULT_DUCKDB_MAX_ROWS = 50
MAX_DUCKDB_MAX_ROWS = 1000
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DUCKDB_ALLOWED_QUERY_PREFIXES = ("select", "with", "show", "describe", "desc", "explain", "values")
DUCKDB_PATH_ENV_NAMES = ("DB_PATH", "db_path")


class LineageFieldInput(BaseModel):
    """单个字段血缘查询入参。"""

    col_name: str = Field(description="字段名")
    col_desc: str = Field(default="", description="字段描述")


class QueryFieldLineageStepInput(BaseModel):
    """字段血缘单步查询工具入参。"""

    table_name: str = Field(description="目标表名，需保留 ${SCHEMA} 这类占位符")
    insert_rank: int = Field(default=1, description="插入顺序")
    fields: list[LineageFieldInput] = Field(min_length=1, description="需要联合分析的字段列表，至少一个")


class QueryFieldLineageUntilStopInput(QueryFieldLineageStepInput):
    """字段血缘自动下钻工具入参。"""

    max_rounds: int = Field(default=DEFAULT_FIELD_LINEAGE_MAX_ROUNDS, description="最大追踪轮次")


class RunDuckDBSQLInput(BaseModel):
    """DuckDB SQL 工具入参。"""

    db_path: str = Field(default="", description="DuckDB 数据库文件路径，支持相对路径、绝对路径或 :memory:；留空时回退到环境变量 DB_PATH/db_path")
    sql: str = Field(description="需要执行的单条只读 SQL，仅允许 SELECT/WITH/SHOW/DESCRIBE/EXPLAIN/VALUES")
    max_rows: int = Field(default=DEFAULT_DUCKDB_MAX_ROWS, ge=1, le=MAX_DUCKDB_MAX_ROWS, description="结果预览行数上限")


def _import_duckdb() -> Any:
    try:
        return importlib.import_module("duckdb")
    except ModuleNotFoundError as exc:
        raise RuntimeError("缺少 duckdb 依赖，请先安装 `duckdb`。") from exc


def _strip_sql_comments(sql: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", sql or "", flags=re.DOTALL)
    text = re.sub(r"(?m)--[^\n]*$", " ", text)
    return text.strip()


def _split_sql_statements(sql: str) -> list[str]:
    statements: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    prev_char = ""

    for char in sql:
        if quote:
            buffer.append(char)
            if char == quote and prev_char != "\\":
                quote = None
            prev_char = char
            continue

        if char in {"'", '"'}:
            quote = char
            buffer.append(char)
            prev_char = char
            continue

        if char == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
            prev_char = char
            continue

        buffer.append(char)
        prev_char = char

    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


def _validate_readonly_duckdb_sql(sql: str) -> tuple[bool, str | None]:
    normalized = _strip_sql_comments(sql)
    if not normalized:
        return False, "SQL 不能为空。"

    statements = _split_sql_statements(normalized)
    if len(statements) != 1:
        return False, "仅允许执行单条 SQL 语句。"

    statement = statements[0].strip()
    keyword_match = re.match(r"([a-zA-Z]+)", statement)
    keyword = keyword_match.group(1).lower() if keyword_match else ""
    if keyword not in DUCKDB_ALLOWED_QUERY_PREFIXES:
        return (
            False,
            "仅允许只读查询，SQL 必须以 SELECT/WITH/SHOW/DESCRIBE/EXPLAIN/VALUES 开头。",
        )

    return True, None


def _get_default_duckdb_path() -> str:
    load_dotenv(_WORKSPACE_ROOT / ".env", override=False)
    load_dotenv(_BACKEND_ROOT / ".env", override=False)
    for env_name in DUCKDB_PATH_ENV_NAMES:
        value = os.getenv(env_name, "").strip()
        if value:
            return value
    return ""


def _resolve_duckdb_database(db_path: str) -> tuple[str, str]:
    raw_path = (db_path or "").strip()
    if not raw_path:
        raw_path = _get_default_duckdb_path()
    if not raw_path:
        raise ValueError("db_path 不能为空，且环境变量 DB_PATH/db_path 未设置。")
    if raw_path == ":memory:":
        return raw_path, raw_path

    expanded = Path(raw_path).expanduser()
    if expanded.is_absolute():
        resolved = expanded.resolve()
        return str(resolved), str(resolved)

    candidates: list[Path] = []
    try:
        sandbox = get_current_session_sandbox()
    except RuntimeError:
        sandbox = None

    if sandbox is not None:
        candidates.append(sandbox.workspace_path / expanded)
    candidates.append(Path.cwd() / expanded)
    candidates.append(_WORKSPACE_ROOT / expanded)

    for candidate in candidates:
        if candidate.exists():
            resolved = candidate.resolve()
            return str(resolved), str(resolved)

    fallback = candidates[0] if candidates else expanded
    resolved = fallback.resolve()
    return str(resolved), str(resolved)


def _render_sql_preview_csv(columns: list[str], rows: list[tuple[Any, ...]]) -> str:
    if not columns:
        return "(查询执行成功，无结果集)\n"

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    return buffer.getvalue() or "(查询执行成功，无结果集)\n"


def _stringify_sql_value(value: Any) -> str:
    normalized = make_json_safe(value)
    if normalized is None:
        return "NULL"
    if isinstance(normalized, (dict, list)):
        try:
            return json.dumps(normalized, ensure_ascii=False)
        except Exception:
            return str(normalized)
    return str(normalized)


def _escape_markdown_table_cell(value: Any) -> str:
    return _stringify_sql_value(value).replace("|", "\\|").replace("\n", "<br/>")


def _build_sql_preview_records(columns: list[str], rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    if not columns:
        return []

    records: list[dict[str, Any]] = []
    for row in rows:
        record = {column: make_json_safe(row[index]) if index < len(row) else None for index, column in enumerate(columns)}
        records.append(record)
    return records


def _render_sql_preview_markdown(columns: list[str], rows: list[tuple[Any, ...]]) -> str:
    if not columns:
        return "(查询执行成功，无结果集)"

    header = "| " + " | ".join(_escape_markdown_table_cell(column) for column in columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(_escape_markdown_table_cell(row[index] if index < len(row) else None) for index, _ in enumerate(columns)) + " |"
        for row in rows
    ]
    return "\n".join([header, separator, *body])


def _build_sql_summary_text(columns: list[str], total_rows: int, preview_rows: int) -> str:
    if not columns:
        return "返回结果：查询执行成功，无结果集。"

    if total_rows > preview_rows:
        return f"返回结果：共{total_rows}行，{len(columns)}个字段，预览如下（仅展示前{preview_rows}行）："
    return f"返回结果：共{total_rows}行，{len(columns)}个字段，预览如下："


def _resolve_sql_export_dir() -> Path:
    try:
        sandbox = get_current_session_sandbox()
    except RuntimeError:
        sandbox = None

    base_dir = sandbox.workspace_path if sandbox is not None else Path.cwd()
    export_dir = base_dir / "sql_exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    return export_dir


def _persist_sql_result_csv(columns: list[str], rows: list[tuple[Any, ...]], sql: str) -> str | None:
    if not columns:
        return None

    keyword_match = re.match(r"([a-zA-Z]+)", _strip_sql_comments(sql))
    prefix = (keyword_match.group(1).lower() if keyword_match else "query")[:16]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_path = _resolve_sql_export_dir() / f"{prefix}_result_{timestamp}_{uuid4().hex[:8]}.csv"
    with export_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(rows)
    return str(export_path.resolve())


def _is_numberish(value: Any) -> bool:
    try:
        float(value)
        return True
    except Exception:
        return False


def _fmt_float(value: float, nd: int = 2) -> str:
    try:
        return f"{float(value):.{nd}f}"
    except Exception:
        return str(value)


def _build_sql_digest(columns: list[str], rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    def column(index: int) -> list[Any]:
        return [row[index] for row in rows]

    n_rows = len(rows)
    period: list[str] = []
    keyvals: dict[str, Any] = {}

    date_idx = None
    for index, column_name in enumerate(columns):
        lowered = column_name.lower()
        if any(token in lowered for token in ("date", "month", "quarter", "as_of", "time")):
            date_idx = index
            break

    if date_idx is not None and n_rows > 0:
        try:
            values = [str(item) for item in column(date_idx) if item is not None]
            if values:
                period = [min(values), max(values)]
        except Exception:
            period = []

    for index, _ in enumerate(columns):
        colvals = column(index) if n_rows > 0 else []
        sample = [value for value in colvals[: min(20, len(colvals))] if value is not None]
        if sample and all(_is_numberish(value) for value in sample):
            try:
                numbers = [float(value) for value in colvals if value is not None]
                if numbers:
                    keyvals["mean"] = sum(numbers) / len(numbers)
                    keyvals["last"] = float(numbers[-1])
            except Exception:
                pass
            break

    label = f"sql|n_rows={n_rows}"
    if period:
        label += f"|{period[0]}..{period[-1]}"
    if "mean" in keyvals:
        label += f"|mean≈{_fmt_float(keyvals['mean'])}"

    digest: dict[str, Any] = {
        "label": label,
        "rows": n_rows,
        "columns": columns,
        "column_count": len(columns),
    }
    if period:
        digest["period"] = period
    if keyvals:
        digest["keyvals"] = keyvals
    return digest


class DuckDBRunner:
    """最小 DuckDB 只读执行器。"""

    def __init__(self, db_path: str = "", sql_limit_rows: int = DEFAULT_DUCKDB_MAX_ROWS):
        self.db_path = db_path
        self.sql_limit_rows = max(1, min(int(sql_limit_rows), MAX_DUCKDB_MAX_ROWS))

    def connect(self) -> tuple[Any, Any, str]:
        duckdb = _import_duckdb()
        database, display_path = _resolve_duckdb_database(self.db_path)
        connect_kwargs: dict[str, Any] = {"database": database}
        if database != ":memory:":
            if not Path(database).exists():
                raise FileNotFoundError(f"DuckDB 数据库文件不存在：{database}")
            connect_kwargs["read_only"] = True
        connection = duckdb.connect(**connect_kwargs)
        cursor = connection.cursor()
        return connection, cursor, display_path

    def disconnect(self, connection: Any, cursor: Any) -> None:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    def execute(self, sql: str, meta: dict[str, Any] | None = None) -> tuple[bool, dict[str, Any], str | None, str | None]:
        del meta
        connection = None
        cursor = None

        is_valid, validation_error = _validate_readonly_duckdb_sql(sql)
        if not is_valid:
            return False, {}, None, validation_error

        try:
            connection, cursor, resolved_db_path = self.connect()
            cursor.execute(sql)
            rows = cursor.fetchall()
            columns = [item[0] for item in cursor.description] if cursor.description else []
            preview_rows = rows[: self.sql_limit_rows]
            sql_result_output = _render_sql_preview_csv(columns, preview_rows)
            if not sql_result_output.endswith("\n"):
                sql_result_output += "\n"
            if len(rows) > self.sql_limit_rows:
                sql_result_output += f"等{len(rows)}行数据，此处仅展示前{self.sql_limit_rows}行数据\n"
            else:
                sql_result_output += f"共{len(rows)}行数据\n"

            digest = _build_sql_digest(columns, rows)
            digest["db_path"] = resolved_db_path
            digest["preview_rows"] = len(preview_rows)
            digest["preview_records"] = _build_sql_preview_records(columns, preview_rows)
            digest["preview_markdown"] = _render_sql_preview_markdown(columns, preview_rows)
            digest["summary_text"] = _build_sql_summary_text(columns, len(rows), len(preview_rows))
            digest["result_file_path"] = _persist_sql_result_csv(columns, rows, sql)
            return True, digest, sql_result_output, None
        except Exception:
            return False, {}, None, f"SQL执行异常：{traceback.format_exc()}"
        finally:
            self.disconnect(connection, cursor)


def _post_json(url: str, payload: dict[str, Any], timeout: float = 360.0) -> dict[str, Any]:
    """发送 JSON POST 请求并返回解析后的 JSON。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url=url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=timeout) as resp:
            status_code = resp.getcode()
            raw_body = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "error": f"HTTP {exc.code}",
            "details": detail,
        }
    except error.URLError as exc:
        return {
            "ok": False,
            "error": "NetworkError",
            "details": str(exc.reason),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": "RequestError",
            "details": str(exc),
        }

    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "error": "InvalidJSON",
            "details": f"HTTP {status_code}, body={raw_body}, parse_error={exc}",
        }

    if not isinstance(parsed, dict):
        return {
            "ok": False,
            "error": "InvalidResponseType",
            "details": f"HTTP {status_code}, expected object JSON but got {type(parsed).__name__}",
        }

    parsed["ok"] = True
    parsed["_status_code"] = status_code
    return parsed


def _normalize_insert_rank(value: Any, default: int = 1) -> int:
    """将 insert_rank 归一化为整数，非法值回退到 default。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_lineage_fields(fields: Any) -> list[dict[str, str]]:
    """将 fields 归一化为 [{"col_name": "...", "col_desc": "..."}]。"""
    if not isinstance(fields, list):
        return []

    normalized: list[dict[str, str]] = []
    for item in fields:
        if isinstance(item, BaseModel):
            data = item.model_dump()
        elif isinstance(item, dict):
            data = item
        else:
            data = {
                "col_name": getattr(item, "col_name", ""),
                "col_desc": getattr(item, "col_desc", ""),
            }

        col_name = str(data.get("col_name", "")).strip()
        if not col_name:
            continue
        normalized.append(
            {
                "col_name": col_name,
                "col_desc": str(data.get("col_desc", "")).strip(),
            }
        )

    return normalized


def _normalize_from_tables(from_tables: Any) -> list[dict[str, Any]]:
    """将上游表列表归一化为标准结构。"""
    if not isinstance(from_tables, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in from_tables:
        if not isinstance(item, dict):
            continue
        from_table = str(item.get("from_table", "")).strip()
        if not from_table:
            continue
        normalized.append(
            {
                "from_table": from_table,
                "insert_rank": _normalize_insert_rank(item.get("insert_rank"), default=1),
            }
        )
    return normalized


def _normalize_target_entity(target_entity: Any, *, table_name: str, insert_rank: int) -> dict[str, Any]:
    """归一化 target_entity，保证 analysis/table_name/insert_rank 始终可用。"""
    if not isinstance(target_entity, dict):
        target_entity = {}

    normalized = dict(target_entity)
    normalized["analysis"] = str(normalized.get("analysis", "")).strip()
    normalized["table_name"] = str(normalized.get("table_name", table_name)).strip() or table_name
    normalized["insert_rank"] = _normalize_insert_rank(normalized.get("insert_rank"), default=insert_rank)
    return normalized


def _parse_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _fields_visit_key(fields: list[dict[str, str]]) -> str:
    return json.dumps(fields, ensure_ascii=False, sort_keys=True)


def _print_lineage_log(tag: str, text: str) -> None:
    """同步输出血缘工具执行日志，便于在终端观察实时进度。"""
    print(f"[lineage:{tag}] {text}", flush=True)

@tool
def run_python_code(code: str) -> str:
    """
    在当前会话 sandbox 的独立工作目录中执行 Python 代码。

    代码运行目录固定为 sandbox/workspace。需要输出结果时请显式使用 print(...)。
    默认复用当前后端正在使用的 Python 解释器与其已安装依赖。
    图表、计算、数据处理等任务先直接写并运行代码，不要预先执行 pip install。
    第三方依赖需要由部署环境预先提供；若缺失，应回退为标准库或可直接生成的文本/HTML/SVG 产物。
    运行时默认只允许访问当前 session sandbox；session 外的读写、删除、子进程创建、网络访问和 ctypes 动态库加载都会被阻止。
    """
    settings = get_settings()
    sandbox = get_current_session_sandbox()
    result = sandbox.run_python_code(
        code=code,
        timeout_seconds=settings.sandbox_command_timeout_seconds,
        output_char_limit=settings.sandbox_output_char_limit,
    )
    return json.dumps(result, ensure_ascii=False)


@tool(args_schema=RunDuckDBSQLInput)
def run_duckdb_sql(db_path: str = "", sql: str = "", max_rows: int = DEFAULT_DUCKDB_MAX_ROWS) -> str:
    """执行单条只读 DuckDB SQL，并返回 JSON 结果，包含摘要、结构化预览与可直接展示的 summary_text。"""
    runner = DuckDBRunner(db_path=db_path, sql_limit_rows=max_rows)
    ok, digest, preview, error = runner.execute(sql, meta={})
    payload = {
        "ok": ok,
        "db_path": db_path,
        "resolved_db_path": digest.get("db_path") if digest else None,
        "sql": sql,
        "summary_text": digest.get("summary_text") if digest else None,
        "file_path": digest.get("result_file_path") if digest else None,
        "result_file_path": digest.get("result_file_path") if digest else None,
        "preview_records": digest.get("preview_records") if digest else None,
        "preview_markdown": digest.get("preview_markdown") if digest else None,
        "digest": digest,
        "preview": preview,
        "error": error,
    }
    return json.dumps(payload, ensure_ascii=False)


@tool(args_schema=QueryFieldLineageStepInput)
def query_field_lineage_step(table_name: str, fields: list[dict[str, str]], insert_rank: int = 1) -> str:
    """调用字段血缘接口，返回单轮结果（message 字段使用 target_entity.analysis）。"""
    settings = get_settings()
    rank = _normalize_insert_rank(insert_rank, default=1)
    normalized_fields = _normalize_lineage_fields(fields)
    field_names = [item["col_name"] for item in normalized_fields]
    _print_lineage_log("call", f"table_name={table_name}, fields={field_names}, insert_rank={rank}")

    if not normalized_fields:
        return json.dumps(
            {
                "ok": False,
                "error": "InvalidFields",
                "details": "fields 至少需要一个合法字段，格式为 [{'col_name': '...', 'col_desc': '...'}]",
            },
            ensure_ascii=False,
        )

    payload = {
        "table_name": table_name,
        "insert_rank": rank,
        "fields": normalized_fields,
    }
    response = _post_json(
        settings.field_lineage_endpoint,
        payload,
        timeout=settings.field_lineage_timeout_seconds,
    )
    if not response.get("ok"):
        _print_lineage_log(
            "error",
            f"table_name={table_name}, fields={field_names}, insert_rank={rank}, error={response.get('error')}, details={response.get('details')}",
        )
        return json.dumps(response, ensure_ascii=False)

    response_fields = _normalize_lineage_fields(response.get("fields"))
    if not response_fields:
        response_fields = normalized_fields

    from_tables = _normalize_from_tables(response.get("from_tables"))
    field_count = _parse_int(response.get("field_count"), default=len(response_fields))
    from_count = _parse_int(response.get("from_count"), default=len(from_tables))
    resolved_table_name = str(response.get("table_name", table_name)).strip() or table_name
    target_entity = _normalize_target_entity(
        response.get("target_entity"),
        table_name=resolved_table_name,
        insert_rank=rank,
    )
    analysis = str(target_entity.get("analysis", "")).strip()
    if analysis:
        _print_lineage_log("analysis", analysis)
    else:
        _print_lineage_log("analysis", "(empty)")

    result = {
        "ok": True,
        "table_name": resolved_table_name,
        "insert_rank": _normalize_insert_rank(response.get("insert_rank"), default=rank),
        "fields": response_fields,
        "field_count": field_count,
        "from_tables": from_tables,
        "from_count": from_count,
        "target_entity": target_entity,
        "analysis": analysis,
        "message": analysis,
        "should_continue": bool(from_count > 0 and from_tables),
        "stop_message": FIELD_LINEAGE_STOP_MESSAGE,
    }
    return json.dumps(result, ensure_ascii=False)


@tool(args_schema=QueryFieldLineageUntilStopInput)
def query_field_lineage_until_stop(
    table_name: str,
    fields: list[dict[str, str]],
    insert_rank: int = 1,
    max_rounds: int = DEFAULT_FIELD_LINEAGE_MAX_ROUNDS,
) -> str:
    """按返回的 from_tables 迭代查询，直到 from_count=0 或达到最大轮次。"""
    rounds = max(1, min(max_rounds, 100))
    messages: list[str] = []
    round_records: list[dict[str, Any]] = []
    normalized_fields = _normalize_lineage_fields(fields)
    if not normalized_fields:
        return json.dumps(
            {
                "ok": False,
                "error": "InvalidFields",
                "details": "fields 至少需要一个合法字段，格式为 [{'col_name': '...', 'col_desc': '...'}]",
            },
            ensure_ascii=False,
        )

    fields_key = _fields_visit_key(normalized_fields)
    visited: set[tuple[str, str, int]] = set()
    visited_table_set: set[str] = set()
    visited_tables: list[str] = []
    related_table_set: set[str] = set()
    related_tables: list[str] = []
    initial_rank = _normalize_insert_rank(insert_rank, default=1)
    pending: list[dict[str, Any]] = [
        {
            "table_name": table_name,
            "fields": normalized_fields,
            "insert_rank": initial_rank,
        }
    ]
    round_count = 0

    while pending and round_count < rounds:
        current = pending.pop(0)
        visit_key = (current["table_name"], fields_key, current["insert_rank"])
        if visit_key in visited:
            continue
        visited.add(visit_key)
        round_count += 1
        if current["table_name"] not in visited_table_set:
            visited_table_set.add(current["table_name"])
            visited_tables.append(current["table_name"])
        if current["table_name"] not in related_table_set:
            related_table_set.add(current["table_name"])
            related_tables.append(current["table_name"])
        _print_lineage_log(
            "round",
            (
                f"round={round_count}, table_name={current['table_name']}, "
                f"fields={[item['col_name'] for item in current['fields']]}, insert_rank={current['insert_rank']}"
            ),
        )

        raw = query_field_lineage_step.invoke(
            {
                "table_name": current["table_name"],
                "fields": current["fields"],
                "insert_rank": current["insert_rank"],
            }
        )
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return json.dumps(
                {
                    "ok": False,
                    "error": "ToolParseError",
                    "details": raw,
                },
                ensure_ascii=False,
            )

        if not parsed.get("ok"):
            return json.dumps(parsed, ensure_ascii=False)

        analysis = str(parsed.get("analysis", "")).strip()
        collected_messages: list[str] = []
        if analysis:
            messages.append(analysis)
            collected_messages.append(analysis)

        current_count = _parse_int(parsed.get("from_count"), default=0)

        from_tables = parsed.get("from_tables")
        from_table_decisions: list[dict[str, Any]] = []
        enqueued = 0
        if isinstance(from_tables, list):
            for item in from_tables:
                if not isinstance(item, dict):
                    continue
                next_name = str(item.get("from_table", "")).strip()
                next_rank = _normalize_insert_rank(item.get("insert_rank"), default=1)
                should_drill_down = bool(next_name)
                reason = "from_tables 返回上游表，继续下钻。" if should_drill_down else "缺少上游表名，无法继续下钻。"

                decision = {
                    "from_table": next_name,
                    "insert_rank": next_rank,
                    "should_drill_down": should_drill_down,
                    "reason": reason,
                }
                from_table_decisions.append(decision)
                if next_name and next_name not in related_table_set:
                    related_table_set.add(next_name)
                    related_tables.append(next_name)
                _print_lineage_log(
                    "decision",
                    (
                        f"from_table={next_name or '(empty)'}, insert_rank={next_rank}, "
                        f"should_drill_down={should_drill_down}, reason={reason}"
                    ),
                )

                if not next_name:
                    continue
                if should_drill_down:
                    pending.append(
                        {
                            "table_name": next_name,
                            "fields": current["fields"],
                            "insert_rank": next_rank,
                        }
                    )
                    enqueued += 1

        round_records.append(
            {
                "round": round_count,
                "query": {
                    "table_name": current["table_name"],
                    "fields": current["fields"],
                    "insert_rank": current["insert_rank"],
                },
                "from_count": current_count,
                "messages": collected_messages,
                "from_table_decisions": from_table_decisions,
                "entity_decisions": from_table_decisions,
                "enqueued_next_queries": enqueued,
            }
        )

    stopped = not pending
    remaining_queries = len(pending)
    _print_lineage_log(
        "summary",
        (
            f"rounds={round_count}, analysis_count={len(messages)}, "
            f"stopped={stopped}, remaining_queries={remaining_queries}, "
            f"visited_tables={visited_tables}, related_tables={related_tables}"
        ),
    )

    return json.dumps(
        {
            "ok": True,
            "table_name": table_name,
            "fields": normalized_fields,
            "field_count": len(normalized_fields),
            "insert_rank": initial_rank,
            "messages": messages,
            "analysis_count": len(messages),
            "logic_summary_count": len(messages),
            "rounds": round_count,
            "stopped": stopped,
            "stop_message": FIELD_LINEAGE_STOP_MESSAGE,
            "details": "" if stopped else "Reached max_rounds before lineage traversal completed.",
            "remaining_queries": remaining_queries,
            "visited_tables": visited_tables,
            "related_tables": related_tables,
            "round_records": round_records,
            "agent_summary_required": True,
            "agent_summary_hint": "请基于 messages、round_records、related_tables、rounds、stopped 输出最终血缘追踪总结。",
        },
        ensure_ascii=False,
    )
