# ============================================================
# 文件名: workbench_database_projection_v0001.py
# 中文名: 真实数据库到二维记录投影
# 版本号: v0001
#
# 主层级: action
# 层级: workbench / adapters / database
# 脚本定位: 隔离进程中的数据库只读适配器
#
# 职责说明:
# - 只读扫描选定数据表，流式生成有来源键的派生二维记录索引
#
# 本脚本做什么:
# - 校验表和主键；保留复合键；写入工作台私有缓存的临时 JSONL 后原子发布
# - Chroma 读取其 SQLite 元数据，不启动可能迁移或写库的 PersistentClient
#
# 本脚本不做什么:
# - 不修改原库，不推断语义关系，不执行模型请求；只写指定派生缓存
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
# alias: workbench_database_projection_v0001
# family: workbench_database_projection
# role: readonly_multi_database_reader
# version: v0001
# status: active
# entry_point: frontend/local_ai_workbench/adapters/workbench_database_projection_v0001.py
# input:
#   - stdin JSON source, table, key columns and private output path
# output:
#   - derived JSONL with original keys and JSON completion result
# depends_on:
#   - Python stdlib
#   - duckdb
# used_by:
#   - workbench_database_adapter_v0001
# ============================================================

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "workbench_database_projection"
SCRIPT_NAME = "workbench_database_projection_v0001"
SCRIPT_VERSION = "v0001"


# ============================================================
# 原始表只读连接；派生记录网格不代表语义聚类
# ============================================================

def quoted(value):
    return '"' + value.replace('"', '""') + '"'


def export(request):
    source = Path(request["path"]).resolve(strict=True)
    driver = request["driver"]
    if driver == "chroma":
        source = source / "chroma.sqlite3" if source.is_dir() else source
    connection = None
    output = Path(request["output"])
    temporary = output.with_suffix(".partial")
    try:
        if driver == "duckdb":
            import duckdb
            connection = duckdb.connect(str(source), read_only=True, config={"enable_external_access": "false", "autoload_known_extensions": "false", "autoinstall_known_extensions": "false"})
            connection.execute("BEGIN TRANSACTION")
            tables = connection.execute("SELECT table_schema, table_name FROM information_schema.tables").fetchall()
        else:
            connection = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            tables = [("main", row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        table, schema = request["table"], request.get("schema", "main")
        if (schema, table) not in tables:
            raise ValueError("Table is not in source catalog")
        name = quoted(schema) + "." + quoted(table)
        if driver == "duckdb":
            columns = [row[0] for row in connection.execute("DESCRIBE " + name).fetchall()]
            constraints = connection.execute("SELECT constraint_column_names FROM duckdb_constraints() WHERE schema_name=? AND table_name=? AND constraint_type='PRIMARY KEY'", [schema, table]).fetchall()
            primary = constraints[0][0] if constraints else []
        else:
            info = connection.execute("PRAGMA " + quoted(schema) + ".table_info(" + quoted(table) + ")").fetchall()
            columns = [row[1] for row in info]
            primary = [row[1] for row in sorted(info, key=lambda row: row[5]) if row[5]]
        keys = request.get("keys") or primary
        if not keys or not isinstance(keys, list) or not all(key in columns for key in keys):
            raise ValueError("Select stable unique key columns for a table without a primary key")
        if len(keys) > 12:
            raise ValueError("Too many key columns")
        if connection.execute("SELECT 1 FROM " + name + " WHERE " + " OR ".join(quoted(k) + " IS NULL" for k in keys) + " LIMIT 1").fetchone():
            raise ValueError("Identity contains null")
        if connection.execute("SELECT 1 FROM " + name + " GROUP BY " + ",".join(map(quoted, keys)) + " HAVING COUNT(*)>1 LIMIT 1").fetchone():
            raise ValueError("Selected identity is not unique")
        labels = [key for key in ("title", "name", "text", "content", "label", "document", "task_name", "function_name") if key in columns]
        fields = list(dict.fromkeys(keys + labels[:1]))
        cursor = connection.execute("SELECT " + ",".join(map(quoted, fields)) + " FROM " + name + " ORDER BY " + ",".join(map(quoted, keys)))
        output.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with temporary.open("w", encoding=DEFAULT_ENCODING) as stream:
            while True:
                rows = cursor.fetchmany(512)
                if not rows:
                    break
                for row in rows:
                    values = dict(zip(fields, row))
                    identity = {key: values[key] for key in keys}
                    if not all(isinstance(v, (str, int, float)) for v in identity.values()):
                        raise ValueError("Unsupported key type")
                    canonical = json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                    object_id = str(row[0]) if len(keys) == 1 and len(str(row[0])) <= 128 else "record:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                    label = str(values[labels[0]])[:160] if labels else object_id
                    record = {"id": object_id, "x": (count % 256) * 12, "y": (count // 256) * 12, "label": label, "kind": "database_record", "provenance": {"driver": driver, "source": str(source), "table": table, "schema": schema, "keys": identity}, "layout": "ordered-record-grid"}
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    count += 1
        os.replace(temporary, output)
        return {"status": "completed", "count": count, "keys": keys, "layout": "ordered-record-grid", "read_only_source": True}
    finally:
        if connection is not None:
            connection.close()


# ============================================================
# CLI / main 接口区；导入不执行
# ============================================================

def main():
    try:
        result = export(json.loads(sys.stdin.read(262145)))
        print(json.dumps(result))
        return 0
    except Exception as error:
        print(json.dumps({"error": {"code": "DATABASE_PROJECTION", "message": str(error) if isinstance(error, ValueError) else "Source projection failed"}}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
