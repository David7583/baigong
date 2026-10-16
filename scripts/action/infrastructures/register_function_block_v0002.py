#!/usr/bin/env python3
# ============================================================
# File: register_function_block_v0002.py
# 中文名: 功能块晋升入库器
# Version: v0002
# Layer: infrastructures
# Main Layer: action
# Updatable: True
#
# Purpose
# 将经过重复调用、已经形成稳定复用价值的任务晋升为功能块。
#
# What it does
# 1) 强制要求真实 source_task_id 和 reuse_evidence；
# 2) 读取来源任务的有序功能 Flow；
# 3) 登记功能块身份和结构化复用依据；
# 4) 将来源任务的功能 Flow 快照为功能块成员；
# 5) 可登记功能块之间的派生关系；
# 6) 全部数据库写入使用同一事务。
#
# What it does NOT do
# 1) 不创建任务或功能；
# 2) 不接受调用方任意拼装成员；
# 3) 不把新任务自动转换成功能块；
# 4) 不伪造调用次数或复用证据；
# 5) 不修改既有功能块。
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: register_function_block
# family: register_function_block
# role: function_block_writer
# version: v0002
# status: active
# entry_point: scripts/action/infrastructures/register_function_block_v0002.py
# input:
#   - --name (required, CLI)
#   - --description (optional, CLI)
#   - --source-task (required, task_id from task_registry)
#   - --reuse-evidence (required, human/agent-reviewed reuse basis)
#   - --parents (optional, comma-separated parent block_ids)
#   - --by (optional, CLI)
#   - --reason (optional, CLI)
#   - --dry-run (optional, CLI)
# output:
#   - sql/action.db (function_block_registry / membership / composition)
#   - logs/register_function_block.jsonl (append-only event log)
#   - actioning/register_function_block/run_meta.json
# depends_on:
#   - init_action_db_v0001
#   - init_action_schema_v0002
#   - register_task_v0002
# used_by:
#   - flow_register_function_block_v0001 (future)
# ============================================================

from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


ACTION_DB_PATH = Path("sql/action.db")
EVENT_LOG_PATH = Path("logs/register_function_block.jsonl")
RUN_META_PATH = Path("actioning/register_function_block/run_meta.json")

SCRIPT_NAME = "register_function_block_v0002.py"
SCRIPT_VERSION = "v0002"
UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime(UTC_FMT)


def dumps_json(data: Any, *, compact: bool = False) -> str:
    if compact:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(data, ensure_ascii=False, indent=2)


def current_user() -> str:
    try:
        return os.getlogin()
    except OSError:
        return "unknown"


def parse_csv(raw: Optional[str]) -> List[str]:
    values: List[str] = []
    seen = set()
    for piece in (raw or "").split(","):
        value = piece.strip()
        if value and value not in seen:
            values.append(value)
            seen.add(value)
    return values


def append_jsonl(path: Path, event: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(dumps_json(event, compact=True) + "\n")


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(dumps_json(payload), encoding="utf-8")
    temporary.replace(path)


def run_register_function_block(
    block_name: str,
    description: Optional[str] = None,
    source_task_id: Optional[str] = None,
    reuse_evidence: Optional[str] = None,
    parents_raw: Optional[str] = None,
    initiated_by: Optional[str] = None,
    reason: Optional[str] = None,
    dry_run: bool = False,
    *,
    db_path: Path = ACTION_DB_PATH,
    event_log_path: Path = EVENT_LOG_PATH,
    run_meta_path: Path = RUN_META_PATH,
) -> Dict[str, Any]:
    timestamp = utc_now()
    block_name = (block_name or "").strip()
    description = description.strip() if description and description.strip() else None
    source_task_id = (source_task_id or "").strip()
    reuse_evidence = (reuse_evidence or "").strip()
    parents = parse_csv(parents_raw)
    initiated_by = (initiated_by or current_user()).strip() or "unknown"
    reason = reason.strip() if reason and reason.strip() else None

    result: Dict[str, Any] = {
        "status": "ok",
        "dry_run": dry_run,
        "timestamp": timestamp,
        "script": SCRIPT_NAME,
        "script_version": SCRIPT_VERSION,
        "block_id": None,
        "block_name": block_name or None,
        "source_task_id": source_task_id or None,
        "reuse_evidence": reuse_evidence or None,
        "functions": [],
        "parents": parents,
        "memberships": 0,
        "compositions": 0,
        "error": None,
        "stage": None,
        "warnings": [],
    }

    if not block_name:
        result.update(status="error", stage="args", error="block name is required")
        return result
    if not source_task_id:
        result.update(
            status="error",
            stage="args",
            error="--source-task is required; a function block must be promoted from a task",
        )
        return result
    if not reuse_evidence:
        result.update(
            status="error",
            stage="args",
            error="--reuse-evidence is required; task creation alone is not promotion evidence",
        )
        return result
    if not db_path.exists():
        result.update(status="error", stage="precheck", error=f"action database not found: {db_path}")
        return result

    block_id = uuid.uuid4().hex
    result["block_id"] = block_id

    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error as exc:
        result.update(status="error", stage="db_connect", error=f"{type(exc).__name__}: {exc}")
        return result

    try:
        block_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(function_block_registry)")
        }
        if "reuse_evidence" not in block_columns:
            result.update(
                status="error",
                stage="schema_check",
                error="function_block_registry.reuse_evidence is missing; apply the v0002 semantic schema first",
            )
            return result

        task_exists = conn.execute(
            "SELECT 1 FROM task_registry WHERE task_id=?",
            (source_task_id,),
        ).fetchone()
        if not task_exists:
            result.update(
                status="error",
                stage="constraint",
                error=f"source task is not registered: {source_task_id}",
            )
            return result

        active_block = conn.execute(
            """SELECT block_id FROM function_block_registry
               WHERE source_task_id=? AND status='active' LIMIT 1""",
            (source_task_id,),
        ).fetchone()
        if active_block:
            result.update(
                status="error",
                stage="constraint",
                error=f"source task already has an active function block: {active_block[0]}",
            )
            return result

        latest_version_row = conn.execute(
            """SELECT version FROM task_versions WHERE task_id=?
               ORDER BY created_at DESC, version DESC LIMIT 1""",
            (source_task_id,),
        ).fetchone()
        if not latest_version_row:
            result.update(
                status="error",
                stage="constraint",
                error="source task has no registered version and cannot be promoted",
            )
            return result
        latest_version = latest_version_row[0]

        functions = [
            row[0]
            for row in conn.execute(
                """SELECT element_id FROM flow_definition
                   WHERE scope='task' AND scope_id=? AND scope_version=?
                     AND element_type='function'
                   ORDER BY order_index, declared_at, flow_id""",
                (source_task_id, latest_version),
            )
        ]
        if not functions:
            result.update(
                status="error",
                stage="constraint",
                error="source task has no registered function flow and cannot be promoted",
            )
            return result
        result["functions"] = functions

        if parents:
            placeholders = ",".join("?" for _ in parents)
            existing_parents = {
                row[0]
                for row in conn.execute(
                    f"SELECT block_id FROM function_block_registry WHERE block_id IN ({placeholders})",
                    parents,
                )
            }
            missing_parents = [parent for parent in parents if parent not in existing_parents]
            if missing_parents:
                result.update(
                    status="error",
                    stage="constraint",
                    error=f"parent block_ids are not registered: {missing_parents}",
                )
                return result

        if dry_run:
            result["memberships"] = len(functions)
            result["compositions"] = len(parents)
            return result

        machine = socket.gethostname()
        conn.execute("BEGIN")
        conn.execute(
            """INSERT INTO function_block_registry
               (block_id, block_name, description, source_task_id, reuse_evidence,
                registered_at, registered_by, registered_where, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
            (
                block_id,
                block_name,
                description,
                source_task_id,
                reuse_evidence,
                timestamp,
                initiated_by,
                machine,
            ),
        )
        for position, function_id in enumerate(functions):
            conn.execute(
                """INSERT INTO function_block_membership
                   (block_id, function_id, position, declared_at)
                   VALUES (?, ?, ?, ?)""",
                (block_id, function_id, position, timestamp),
            )
        for parent_id in parents:
            conn.execute(
                """INSERT INTO function_block_composition
                   (parent_block_id, child_block_id) VALUES (?, ?)""",
                (parent_id, block_id),
            )
        conn.commit()
        result["memberships"] = len(functions)
        result["compositions"] = len(parents)
    except sqlite3.Error as exc:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        result.update(status="error", stage="db_write", error=f"{type(exc).__name__}: {exc}")
        return result
    finally:
        conn.close()

    event = {
        "event": "promote_task_to_function_block",
        "timestamp": timestamp,
        "block_id": block_id,
        "block_name": block_name,
        "source_task_id": source_task_id,
        "reuse_evidence": reuse_evidence,
        "function_count": len(result["functions"]),
        "initiated_by": initiated_by,
        "reason": reason,
        "script": SCRIPT_NAME,
        "script_version": SCRIPT_VERSION,
    }
    try:
        append_jsonl(event_log_path, event)
        write_json(run_meta_path, result)
    except OSError as exc:
        result["warnings"].append(f"audit artifact write failed: {type(exc).__name__}: {exc}")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Promote one repeatedly reused task to a function block."
    )
    parser.add_argument("--name", required=True)
    parser.add_argument("--description", default=None)
    parser.add_argument("--source-task", required=True)
    parser.add_argument("--reuse-evidence", required=True)
    parser.add_argument("--parents", default=None)
    parser.add_argument("--by", default=None)
    parser.add_argument("--reason", default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_register_function_block(
        block_name=args.name,
        description=args.description,
        source_task_id=args.source_task,
        reuse_evidence=args.reuse_evidence,
        parents_raw=args.parents,
        initiated_by=args.by,
        reason=args.reason,
        dry_run=bool(args.dry_run),
    )
    print(dumps_json(result))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
