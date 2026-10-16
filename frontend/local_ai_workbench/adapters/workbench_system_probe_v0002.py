# ============================================================
# 文件名: workbench_system_probe_v0002.py
# 中文名: 工作台运行状态和存储清点
# 版本号: v0002
#
# 主层级: action
# 层级: workbench / adapters / system
# 脚本定位: 本地运行状态的只读观察适配器
#
# 职责说明:
# - 展示新旧流水线真实进度、错误及环境可用性
#
# 本脚本做什么:
# - 只读 SQLite 运行账本；显式清点存储，不删除数据
#
# 本脚本不做什么:
# - 不调用模型，不启动服务，不修改权限或业务库
#
# 制度边界声明:
# - 不输出凭证、聊天正文、原始异常堆栈；不依据 PID 判定服务成功
# - 存储扫描不跟随链接，有时间预算，截断如实标明
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: workbench_system_probe_v0002
# family: workbench_system_probe
# role: read_only_runtime_observation
# version: v0002
# status: active
# entry_point: frontend/local_ai_workbench/adapters/workbench_system_probe_v0002.py
# input:
#   - root and status or storage operation
# output:
#   - sanitized runtime and storage JSON
# depends_on:
#   - Python stdlib
# used_by:
#   - workbench_runtime_control_v0001
# ============================================================
from __future__ import annotations
import argparse
import json
import os
import sqlite3
import time
import hashlib
import ctypes
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "workbench_system_probe"
SCRIPT_NAME = "workbench_system_probe_v0002"
SCRIPT_VERSION = "v0002"


def read_state(file, kind):
    if not file.is_file():
        return {"status": "not_initialized"}
    db = sqlite3.connect(file.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
    try:
        control = dict(db.execute("SELECT k,v FROM control"))
        if kind == "queue":
            sources = [{"id": r[0], "path": r[1], "status": r[2], "run_id": r[3], "error": "recorded_error" if r[4] else None} for r in db.execute("SELECT id,path,status,run_id,error FROM sources ORDER BY updated DESC LIMIT 200")]
            worker = json.loads(control.get("worker", "{}"))
            scan = json.loads(control.get("scan", "{}"))
            return {"source_states": dict(db.execute("SELECT status,COUNT(*) FROM sources GROUP BY status")), "paused": json.loads(control.get("pause", "false")),
                    "sources": sources, "sources_limit": 200, "worker_recorded": {k: worker[k] for k in ("status", "pid", "version") if k in worker},
                    "scan_recorded": {k: scan[k] for k in ("status", "files") if k in scan}, "last_error": "recorded_error" if worker.get("error") or scan.get("error") else None,
                    "observation": "stored_state_not_live_process"}
        if kind == "legacy":
            error = json.loads(control.get("vector_error", "null"))
            return {"indexed_vectors": db.execute("SELECT COUNT(*) FROM vector_index").fetchone()[0],
                    "vector_enabled_recorded": json.loads(control.get("vector_enabled", "false")),
                    "last_error": "CUDA illegal memory access" if error and "illegal memory access" in str(error) else "recorded_error" if error else None,
                    "observation": "stored_state_not_live_process"}
        return {"states": dict(db.execute("SELECT state,COUNT(*) FROM paragraphs GROUP BY state")), "source_cursor": int(control.get("cursor", "0")),
                "scan_complete": json.loads(control.get("scan_complete", "false")), "observation": "stored_state_not_live_process"}
    finally:
        db.close()


def host_resources():
    if os.name != 'nt':
        return {"status": "unavailable", "gpu": "not_observed"}
    class Memory(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [(name, ctypes.c_ulonglong) for name in ('total', 'available', 'total_page', 'available_page', 'total_virtual', 'available_virtual', 'extended')]
    class FileTime(ctypes.Structure):
        _fields_ = [('low', ctypes.c_ulong), ('high', ctypes.c_ulong)]
    def cpu():
        ticks = [FileTime() for _ in range(3)]
        if not ctypes.windll.kernel32.GetSystemTimes(*(ctypes.byref(x) for x in ticks)):
            raise OSError('cpu_probe_failed')
        return [x.low + (x.high << 32) for x in ticks]
    memory = Memory(); memory.length = ctypes.sizeof(Memory)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
        raise OSError('memory_probe_failed')
    before = cpu(); time.sleep(.1); after = cpu()
    idle, kernel, user = [b-a for a,b in zip(before, after)]
    total = kernel + user
    return {"status": "observed", "cpu_percent": round(max(0, min(100, (1-idle/total)*100)), 1) if total else None,
            "memory_total": memory.total, "memory_available": memory.available, "gpu": "not_observed", "scope": "host_snapshot"}


def status(root):
    result = {"status": "completed", "observed_at": datetime.now(timezone.utc).isoformat(), "pipelines": {}}
    for name, file, kind in (("ingestion", "runtime/concurrent_pipeline/queue.sqlite3", "queue"),
                             ("legacy_vectors", "runtime/concurrent_pipeline/derived.sqlite3", "legacy"),
                             ("paragraph_vectors", "runtime/anchor_vectors_v0001/ledger.sqlite3", "paragraph")):
        try:
            result["pipelines"][name] = read_state(root / file, kind)
        except Exception as exc:
            result["pipelines"][name] = {"status": "unavailable", "error_type": type(exc).__name__}
    result["databases"] = {}
    for name, file, tables in (("source", "sql/action/data.db", ("data_text_units", "data_text_unit_source_timestamps", "data_text_unit_source_identities")),
                               ("action", "sql/action/action_data.db", ("concept_units", "instance_units"))):
        path = root / file
        if not path.is_file():
            result["databases"][name] = {"status": "not_initialized", "path": file}
            continue
        db = None
        try:
            db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
            deadline = time.monotonic() + 3
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
            counts = {table: db.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0] for table in tables}
            result["databases"][name] = {"status": "available", "path": file, "counts": counts, "observation": "read_only_count"}
        except Exception as exc:
            result["databases"][name] = {"status": "unavailable", "path": file, "error_type": type(exc).__name__}
        finally:
            if db is not None:
                db.close()
    try:
        result["resources"] = host_resources()
    except Exception as exc:
        result["resources"] = {"status": "unavailable", "error_type": type(exc).__name__, "gpu": "not_observed"}
    result["environment"] = {name: (root / file).is_file() for name, file in (
        ("python", ".venv/Scripts/python.exe"), ("pythonw", ".venv/Scripts/pythonw.exe"),
        ("electron", "frontend/local_ai_workbench/node_modules/electron/dist/electron.exe"),
        ("tokenizer", "model/Qwen3-VL-Embedding-2B/tokenizer.json"))}
    agents = root / "config/action/ai/action_ai_agents_v0001.json"
    if agents.exists():
        try:
            result["agent_dependencies"] = {name: {"executable_available": (root / value["executable"]).is_file(),
                                                    "script_available": not value.get("script") or Path(value["script"]).is_file(),
                                                    "portable": not Path(value["executable"]).is_absolute()}
                                        for name, value in json.loads(agents.read_text(DEFAULT_ENCODING))["agents"].items()}
        except Exception as exc:
            result["agent_dependencies"] = {"status": "unavailable", "error_type": type(exc).__name__}
    return result


def storage(root):
    deadline = time.monotonic() + 25
    groups, skipped, truncated = [], 0, False
    labels = {"data": "原始资料", "sql": "正式事实库", "model": "模型权重", "chroma": "向量索引", "duckdb": "分析投影",
              "actioning": "流水线产物及保存待办", "scripts": "源码及历史流水线输出", "runtime": "运行状态、缓存、恢复备份", ".deployment_backups": "发布回退备份"}
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or folder.is_symlink():
            continue
        size = count = 0
        for base, dirs, files in os.walk(folder, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(base) / name).is_symlink()]
            if time.monotonic() > deadline:
                truncated = True; break
            for name in files:
                try:
                    file = Path(base) / name
                    if not file.is_symlink():
                        size += file.stat().st_size; count += 1
                except OSError:
                    skipped += 1
        groups.append({"directory": folder.name, "bytes": size, "files": count, "purpose": labels.get(folder.name, "环境或配置"), "deletion_performed": False})
        if truncated:
            break
    return {"status": "partial" if truncated or skipped else "completed", "groups": groups, "truncated": truncated,
            "skipped_files": skipped, "total_bytes_observed": sum(x["bytes"] for x in groups), "cleanup": "preview_only_no_deletion"}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--root", required=True)
    parser.add_argument("--operation", choices=["status", "storage"], default="status"); parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--request"); parser.add_argument("--response")
    args = parser.parse_args()
    try:
        root = Path(args.root).resolve()
        if args.request or args.response:
            request, response = Path(args.request).resolve(), Path(args.response).resolve()
            request.relative_to(root / "runtime/intermediate_tasks"); response.relative_to(request.parent)
            if request.is_symlink() or response.is_symlink() or request.stat().st_size > 262144:
                raise ValueError("handoff_path_invalid")
            raw = request.read_bytes(); envelope = json.loads(raw)
            if envelope.get("schema_version") != "backend.observe.v1" or envelope.get("root") != str(root) or envelope.get("producer", {}).get("script") != "workbench_runtime_control_v0002":
                raise ValueError("observation_contract_invalid")
            if envelope.get("run_id") != request.parent.name or envelope.get("operation") not in ("status", "storage"):
                raise ValueError("observation_identity_invalid")
            result = (storage if envelope["operation"] == "storage" else status)(root)
            output = {"schema_version": "backend.observe.v1", "run_id": envelope["run_id"], "producer": {"script": SCRIPT_NAME, "version": SCRIPT_VERSION},
                      "source_sha256": hashlib.sha256(raw).hexdigest(), "status": "completed", "result": result}
            temporary = response.with_suffix(".tmp"); temporary.write_text(json.dumps(output, ensure_ascii=False), DEFAULT_ENCODING); temporary.replace(response)
        else:
            print(json.dumps((storage if args.operation == "storage" else status)(root), ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__})); return 2


if __name__ == "__main__":
    raise SystemExit(main())
