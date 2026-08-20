#!/usr/bin/env python3
# ============================================================
# File: vector_integrity_check_v0002.py
# 中文名: 向量完整性检查脚本
# Version: v0002
# Layer: derivation
# Main Layer: action
# Updatable: True
#
# Purpose:
# 以全局或单一对象类型作用域，审计 SQL、ChromaDB 与状态索引的一致性。
#
# What it does:
# 1. 默认保持 v0001 的全局完整性审计语义
# 2. 支持 concept 或 instance 单目标审计，供逐目标向量编排写后核验
# 3. 所有检查在同一个明确作用域内执行并在报告中记录作用域
#
# What it does NOT do:
# 1. 不修复、不删除、不写入任何业务数据
# 2. 不把单目标通过伪装成全局通过
# 3. 不改变 v0001 的数据读取和比对规则
# ============================================================

# ============================================================
# ALIAS_META
# alias: vector_integrity_check
# family: vector_audit
# role: integrity_checker
# version: v0002
# status: active
# entry_point: scripts/action/vector/vector_integrity_check_v0002.py
# input: SQL database, ChromaDB, state index, explicit audit target
# output: Scope-labelled integrity report and issue logs
# depends_on: scripts/action/vector/vector_integrity_check_v0001.py, chromadb, sqlite3, Python stdlib
# used_by: scripts/orchestration/action/vector_embedding_pipeline_v0002.py, manual audit, scheduled health check
# ============================================================

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional


SCRIPT_NAME = "vector_integrity_check"
SCRIPT_VERSION = "v0002"
MAIN_LAYER = "action"
VALID_TARGETS = ("all", "concept", "instance")
TARGET_ENV = "ACTION_VECTOR_INTEGRITY_TARGET"


def _find_project_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "AGENTS.md").is_file() and (candidate / "scripts").is_dir():
            return candidate
    raise RuntimeError(f"Cannot locate project root from: {start}")


def _load_v0001():
    project_root = _find_project_root(Path(__file__).resolve().parent)
    source = project_root / "scripts" / "action" / "vector" / "vector_integrity_check_v0001.py"
    spec = importlib.util.spec_from_file_location("vector_integrity_check_v0001_base", source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load compatibility base: {source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = _load_v0001()


def _filter_sql(records: Dict[str, str], target: str) -> Dict[str, str]:
    if target == "all":
        return records
    prefix = f"{target}:"
    return {key: value for key, value in records.items() if key.startswith(prefix)}


def _filter_chroma(records: Dict[str, tuple], target: str) -> Dict[str, tuple]:
    if target == "all":
        return records
    return {signature: value for signature, value in records.items() if value[0] == target}


def _filter_index(records: Dict[str, str], target: str) -> Dict[str, str]:
    if target == "all":
        return records
    prefix = f"{target}:"
    return {key: value for key, value in records.items() if key.startswith(prefix)}


def _count_alignment(
    target: str,
    sql_instance: int,
    sql_concept: int,
    chroma_instance: int,
    chroma_concept: int,
) -> Dict[str, Any]:
    details = BASE.check_count_alignment(
        sql_instance, sql_concept, chroma_instance, chroma_concept
    )
    if target == "concept":
        matched = sql_concept == chroma_concept
    elif target == "instance":
        matched = sql_instance == chroma_instance
    else:
        matched = bool(details["instance_match"] and details["concept_match"])
    details.update({
        "status": BASE.STATUS_PASS if matched else BASE.STATUS_FAIL,
        "audit_target": target,
        "scope_match": matched,
    })
    return details


def run_integrity_check(
    sql_db_path: Path,
    chromadb_path: Path,
    state_index_path: Path,
    output_dir: Optional[Path],
    target: str = "all",
    quick_mode: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    if target not in VALID_TARGETS:
        raise ValueError(f"Unsupported audit target: {target}")

    started_at = BASE._utc_iso()
    result: Dict[str, Any] = {
        "status": "ok",
        "timestamp": started_at,
        "audit_target": target,
        "scope": "global" if target == "all" else "single_object_type",
        "summary": {},
        "checks": {},
        "details": {},
        "errors": [],
    }
    sql_conn = BASE._connect_sql(sql_db_path)
    if sql_conn is None:
        result["errors"].append(f"Failed to connect to SQL: {sql_db_path}")
        result["status"] = "error"
    client, collection = BASE._connect_chromadb(chromadb_path)
    if collection is None:
        result["errors"].append(f"Failed to connect to ChromaDB: {chromadb_path}")
        result["status"] = "error"
    if result["status"] == "error":
        if sql_conn is not None:
            sql_conn.close()
        return result

    try:
        sql_instance, sql_concept = BASE._get_sql_counts(sql_conn)
        chroma_instance, chroma_concept = BASE._get_chromadb_counts(collection)
        all_index_records = BASE._get_index_records(state_index_path)
        result["summary"] = {
            "audit_target": target,
            "sql_instance_count": sql_instance,
            "sql_concept_count": sql_concept,
            "sql_total": sql_instance + sql_concept,
            "chromadb_instance_count": chroma_instance,
            "chromadb_concept_count": chroma_concept,
            "chromadb_total": chroma_instance + chroma_concept,
            "index_count": len(all_index_records),
        }
        count_check = _count_alignment(
            target, sql_instance, sql_concept, chroma_instance, chroma_concept
        )
        result["checks"]["count_alignment"] = count_check["status"]
        result["details"]["count_alignment"] = count_check

        if quick_mode:
            for key in (
                "orphan_vectors", "missing_vectors", "signature_drift",
                "index_consistency", "multi_active",
            ):
                result["checks"][key] = "skipped"
        else:
            sql_records = _filter_sql(BASE._get_sql_records(sql_conn), target)
            chroma_records = _filter_chroma(BASE._get_chromadb_records(collection), target)
            index_records = _filter_index(all_index_records, target)
            result["summary"]["scoped_sql_count"] = len(sql_records)
            result["summary"]["scoped_chromadb_count"] = len(chroma_records)
            result["summary"]["scoped_index_count"] = len(index_records)

            checks = (
                ("orphan_vectors", BASE.check_orphan_vectors(sql_records, chroma_records)),
                ("missing_vectors", BASE.check_missing_vectors(sql_records, chroma_records)),
                ("signature_drift", BASE.check_signature_drift(sql_records, chroma_records)),
            )
            for key, (status, details) in checks:
                result["checks"][key] = status
                result["summary"][f"{key.removesuffix('_vectors')}_count"] = len(details)
                if details:
                    result["details"][key] = details

            index_status, index_details = BASE.check_index_consistency(index_records, chroma_records)
            result["checks"]["index_consistency"] = index_status
            result["details"]["index_consistency"] = index_details
            multi_status, multi_details = BASE.check_multi_active(chroma_records)
            result["checks"]["multi_active"] = multi_status
            result["summary"]["multi_active_count"] = len(multi_details)
            if multi_details:
                result["details"]["multi_active"] = multi_details
    finally:
        sql_conn.close()

    if any(value == BASE.STATUS_FAIL for value in result["checks"].values()):
        result["status"] = "issues_found"
    result["finished_at"] = BASE._utc_iso()

    if not dry_run and output_dir:
        try:
            BASE._ensure_dir(output_dir)
            (output_dir / "report.json").write_text(BASE._pretty_json(result), encoding="utf-8")
            for key in ("orphan_vectors", "missing_vectors", "signature_drift"):
                if result["details"].get(key):
                    BASE._write_jsonl(output_dir / f"{key}.jsonl", result["details"][key])
            meta = {
                "script_name": SCRIPT_NAME,
                "script_version": SCRIPT_VERSION,
                "main_layer": MAIN_LAYER,
                "audit_target": target,
                "sql_db_path": str(sql_db_path),
                "chromadb_path": str(chromadb_path),
                "state_index_path": str(state_index_path),
                "quick_mode": quick_mode,
                "started_at": started_at,
                "finished_at": result["finished_at"],
                "status": result["status"],
            }
            (output_dir / "run_meta.json").write_text(BASE._pretty_json(meta), encoding="utf-8")
            result["output_dir"] = str(output_dir)
        except Exception as exc:
            result["errors"].append(f"Failed to write report: {exc}")
            result["status"] = "error"
    return result


def parse_args() -> argparse.Namespace:
    default_target = os.environ.get(TARGET_ENV, "all")
    if default_target not in VALID_TARGETS:
        default_target = "all"
    parser = argparse.ArgumentParser(
        description="Audit SQL, ChromaDB and state-index vector integrity in an explicit scope."
    )
    parser.add_argument("--sql-db", default="sql/action_data.db")
    parser.add_argument("--chromadb-path", default="chromadb/action/action_data/vectors")
    parser.add_argument("--state-index", default="vector/state/active_index.jsonl")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--target", choices=VALID_TARGETS, default=default_target)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir) if args.output_dir else None
    if output_dir is None and not args.dry_run:
        output_dir = Path("vector/audit/integrity_check") / BASE._timestamp_for_path()
    result = run_integrity_check(
        sql_db_path=Path(args.sql_db),
        chromadb_path=Path(args.chromadb_path),
        state_index_path=Path(args.state_index),
        output_dir=output_dir,
        target=args.target,
        quick_mode=args.quick,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("status") == "error":
        return 1
    if result.get("status") == "issues_found":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
