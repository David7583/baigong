# ============================================================
# 文件名: workbench_database_reader_v0001.py
# 中文名: 多数据库只读目录、分页与资料内容适配
# 版本号: v0001
#
# 主层级: action
# 层级: workbench / adapters / database
# 脚本定位: 隔离进程中的数据库只读适配器
#
# 职责说明:
# - 读取 SQLite、DuckDB、Chroma 持久目录并返回结构化目录或分页
#
# 本脚本做什么:
# - 校验既有数据库路径；只读连接；查询已存在表的有限行
# - Chroma 读取其 SQLite 元数据，不启动可能迁移或写库的 PersistentClient
#
# 本脚本不做什么:
# - 不修改数据库，不执行任意 SQL，不执行向量推理或模型请求
#
# 制度边界声明:
# - 原始数据库只读；DuckDB 禁用外部访问；连接在 finally 中释放
# - 不自动迁移 Chroma，不将元数据浏览冒充向量相似度检索
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: workbench_database_reader_v0001
# family: workbench_database_reader
# role: readonly_multi_database_reader
# version: v0001
# status: active
# entry_point: frontend/local_ai_workbench/adapters/workbench_database_reader_v0001.py
# input:
#   - stdin JSON driver, path, operation, table, limit, offset
# output:
#   - stdout JSON catalog or bounded rows with source
# depends_on:
#   - Python stdlib
#   - duckdb
# used_by:
#   - workbench_database_adapter_v0001
# ============================================================

from __future__ import annotations

import argparse
import ctypes
import datetime
import decimal
import json
import sqlite3
import sys
import time
from pathlib import Path


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "workbench_database_reader"
SCRIPT_NAME = "workbench_database_reader_v0001"
SCRIPT_VERSION = "v0001"


# ============================================================
# 异常类型
# ============================================================

class DatabaseReadError(RuntimeError):
    pass


# ============================================================
# 数据结构与工具函数
# ============================================================

def safe_value(value):
    if isinstance(value, bytes):
        return {"binary_bytes": len(value)}
    if isinstance(value, (datetime.date, datetime.datetime, decimal.Decimal)):
        return str(value)
    if isinstance(value, str):
        return value[:1000]
    if isinstance(value, (list, dict, tuple)):
        return str(value)[:1000]
    return value


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def process_memory() -> dict:
    """Read Windows process working set without installing a telemetry dependency."""
    if sys.platform != "win32":
        return {"rss_bytes": None, "peak_rss_bytes": None}
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
            (name, ctypes.c_size_t) for name in (
                "peak", "working", "paged_peak", "paged", "nonpaged_peak",
                "nonpaged", "pagefile", "pagefile_peak")]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return {"rss_bytes": None, "peak_rss_bytes": None}
    return {"rss_bytes": counters.working, "peak_rss_bytes": counters.peak}


# ============================================================
# 核心读取组件
# ============================================================

def read_library(connection, tables, driver, request):
    """Adapt known content formats without guessing arbitrary business tables."""
    available = {(t["schema"], t["name"]) for t in tables}
    chosen = next((name for name in ["data_text_units", "instance_units_latest"] if ("main", name) in available), None)
    if driver == "chroma" and ("main", "embedding_metadata") in available and ("main", "embeddings") in available:
        columns = {d[0] for d in connection.execute("SELECT * FROM embedding_metadata LIMIT 0").description}
        if not {"id", "key", "string_value"} <= columns:
            return {"supported": False, "reason": "unsupported_chroma_metadata"}
        if ("main", "embedding_fulltext_search") in available and connection.execute("SELECT 1 FROM embedding_fulltext_search LIMIT 1").fetchone():
            query = "SELECT e.id, e.embedding_id, substr(m.string_value,1,8001) FROM embedding_fulltext_search m JOIN embeddings e ON e.id=m.rowid WHERE m.string_value IS NOT NULL"
            table = "embedding_fulltext_search"
        else:
            if not connection.execute("SELECT 1 FROM embedding_metadata WHERE key='chroma:document' LIMIT 1").fetchone():
                return {"supported": False, "reason": "documents_not_stored"}
            query = "SELECT e.id, e.embedding_id, substr(m.string_value,1,8001) FROM embedding_metadata m JOIN embeddings e ON e.id=m.id WHERE m.key='chroma:document'"
            table = "embedding_metadata"
        key_columns, text_column = ["id", "embedding_id"], "string_value"
    elif chosen:
        columns = [d[0] for d in connection.execute("SELECT * FROM " + quote_identifier(chosen) + " LIMIT 0").description]
        text_column = next((c for c in ["text", "content", "unit_text", "document"] if c in columns), None)
        if not text_column:
            return {"supported": False, "reason": "text_field_missing"}
        if driver == "duckdb":
            key_columns = [c for c in ["instance_id", "unit_text_id"] if c in columns][:1]
        else:
            info = connection.execute("PRAGMA table_info(" + quote_identifier(chosen) + ")").fetchall()
            key_columns = [row[1] for row in sorted(info, key=lambda row: row[5]) if row[5]]
        if not key_columns:
            return {"supported": False, "reason": "identity_missing"}
        query = "SELECT " + ",".join(map(quote_identifier, key_columns)) + ",substr(" + quote_identifier(text_column) + ",1,8001) FROM " + quote_identifier(chosen) + " WHERE " + quote_identifier(text_column) + " IS NOT NULL"
        table = chosen
    else:
        return {"supported": False, "reason": "content_adapter_missing"}
    result = {"supported": True, "format": table, "read_only": True, "items": []}
    if request.get("profile_only"):
        return result
    search = str(request.get("query", ""))[:200]
    offset = max(0, min(1000000, int(request.get("offset", 0))))
    limit = max(1, min(50, int(request.get("limit", 20))))
    params = []
    if search:
        query += " AND " + ("m.string_value" if driver == "chroma" else quote_identifier(text_column)) + " LIKE ? ESCAPE '\\'"
        params.append("%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    order = "e.id" if driver == "chroma" else ",".join(map(quote_identifier, key_columns))
    rows = connection.execute(query + " ORDER BY " + order + " LIMIT ? OFFSET ?", [*params, limit + 1, offset]).fetchall()
    result.update(offset=offset, has_more=len(rows) > limit)
    for row in rows[:limit]:
        text = str(row[-1] or "")
        result["items"].append({"text": text[:8000], "truncated": len(text) > 8000, "provenance": {"table": "embeddings" if driver == "chroma" else table, "content_table": table, "keys": dict(zip(key_columns, row[:-1]))}})
    return result


def read_database(request: dict) -> dict:
    started = time.perf_counter()
    driver = request.get("driver")
    if driver not in {"sqlite", "duckdb", "chroma"}:
        raise DatabaseReadError("Unsupported driver")
    target = Path(request.get("path", "")).resolve(strict=True)
    if driver == "chroma" and target.is_dir():
        target = target / "chroma.sqlite3"
    if not target.is_file():
        raise DatabaseReadError("Existing database file required")
    connection = None
    try:
        if driver == "duckdb":
            import duckdb
            connection = duckdb.connect(str(target), read_only=True, config={"enable_external_access": "false", "autoload_known_extensions": "false", "autoinstall_known_extensions": "false"})
            tables = [
                {"schema": row[0], "name": row[1], "type": row[2]}
                for row in connection.execute("SELECT table_schema, table_name, table_type FROM information_schema.tables WHERE table_schema NOT IN ('information_schema','pg_catalog') ORDER BY table_schema,table_name").fetchall()
            ]
        else:
            connection = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=2)
            connection.execute("PRAGMA query_only=ON")
            tables = [{"schema": "main", "name": row[0], "type": row[1]} for row in connection.execute("SELECT name,type FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        operation = request.get("operation", "catalog")
        result = {"schema_version": "workbench.database.v1", "driver": driver, "read_only": True, "source": str(target), "tables": tables}
        if driver == "chroma":
            result["access_mode"] = "persistent_metadata_readonly"
            result["vector_search_supported"] = False
            if any(t["name"] == "collections" for t in tables):
                cursor = connection.execute("SELECT * FROM collections LIMIT 100")
                columns = [d[0] for d in cursor.description]
                result["collections"] = [dict(zip(columns, map(safe_value, row))) for row in cursor.fetchall()]
        if operation == "library":
            result["library"] = read_library(connection, tables, driver, request)
        elif operation == "record":
            table, schema, keys = request.get("table"), request.get("schema", "main"), request.get("keys")
            if not any(t["name"] == table and t["schema"] == schema for t in tables):
                raise DatabaseReadError("Table is not in the mounted catalog")
            name = quote_identifier(schema) + "." + quote_identifier(table)
            columns = [d[0] for d in connection.execute("SELECT * FROM " + name + " LIMIT 0").description]
            if not isinstance(keys, dict) or not keys or len(keys) > 12 or not all(key in columns for key in keys):
                raise DatabaseReadError("Invalid source identity")
            cursor = connection.execute("SELECT * FROM " + name + " WHERE " + " AND ".join(quote_identifier(k) + "=?" for k in keys) + " LIMIT 2", list(keys.values()))
            rows = cursor.fetchall()
            if len(rows) != 1:
                raise DatabaseReadError("Source record missing or no longer unique")
            result["record"] = dict(zip(columns, map(safe_value, rows[0])))
            result["identity"] = keys
        elif operation == "preview":
            table = request.get("table")
            schema = request.get("schema", "main")
            if not any(t["name"] == table and t["schema"] == schema for t in tables):
                raise DatabaseReadError("Table is not in the mounted catalog")
            limit = min(200, max(1, int(request.get("limit", 50))))
            offset = max(0, int(request.get("offset", 0)))
            name = quote_identifier(schema) + "." + quote_identifier(table)
            cursor = connection.execute(f"SELECT * FROM {name} LIMIT ? OFFSET ?", (limit + 1, offset))
            columns = [d[0] for d in cursor.description]
            rows = cursor.fetchall()
            result.update({"table": table, "schema": schema, "columns": columns, "rows": [[safe_value(v) for v in row] for row in rows[:limit]], "offset": offset, "limit": limit, "has_more": len(rows) > limit})
        elif operation == "statistics":
            counts = []
            for table in tables:
                table_started = time.perf_counter()
                name = quote_identifier(table["schema"]) + "." + quote_identifier(table["name"])
                count = connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                counts.append({**table, "row_count": count, "count_ms": (time.perf_counter() - table_started) * 1000})
            result["table_counts"] = counts
            result["source_bytes"] = target.stat().st_size
            result["count_semantics"] = "Per-table physical rows; not a sum of unique business objects"
        elif operation != "catalog":
            raise DatabaseReadError("Only catalog, statistics and preview operations are supported")
        result["elapsed_ms"] = (time.perf_counter() - started) * 1000
        result["reader_memory"] = process_memory()
        return result
    finally:
        if connection is not None:
            connection.close()


# ============================================================
# CLI / main 接口区
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        request = json.loads(sys.stdin.read(262145))
        if args.dry_run:
            target = Path(request["path"]).resolve(strict=True)
            result = {"status": "dry-run", "exists": target.exists(), "writes": False}
        else:
            result = read_database(request)
        print(json.dumps(result, ensure_ascii=True, default=safe_value))
        return 0
    except Exception as error:
        print(json.dumps({"error": {"code": type(error).__name__, "message": "Database unavailable or read-only query rejected"}}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
