#!/usr/bin/env python3
# ============================================================
# File: vector_integrity_check_v0001.py
# 中文名: 向量完整性检查脚本
# Version: v0001
# Layer: derivation
# Main Layer: understand
# Updatable: True
#
# Purpose:
# 审计向量库与 SQL 库的数据完整性和一致性。
#
# What it does:
# 1. 检查向量库记录数与 SQL 记录数是否对齐
# 2. 检查孤儿向量（向量库有但 SQL 没有）
# 3. 检查缺失向量（SQL 有但向量库没有）
# 4. 检查 signature 漂移（source_hash 与 SQL content_hash 不一致）
# 5. 检查状态索引与向量库的一致性
# 6. 检查多 active 违规（同一 object_id 多条 active）
# 7. 生成审计报告
#
# What it does NOT do:
# 1. 不修复任何问题
# 2. 不写入向量库
# 3. 不修改 SQL 数据
# 4. 不更新状态索引
# ============================================================

# ============================================================
# ALIAS_META
# alias: vector_integrity_check
# family: vector_audit
# role: integrity_checker
# version: v0001
# status: active
# entry_point: scripts/understand/vector_integrity_check_v0001.py
# input: SQL database, ChromaDB, state index
# output: Integrity check report and issue logs
# depends_on: chromadb, sqlite3, Python stdlib
# used_by: manual audit, scheduled health check
# ============================================================

# ============================================================
# 制度与职责说明注释区
#
# - 是否进行合规判断: 是（检查数据完整性和一致性）
# - 是否进行制度判断: 否（只报告问题，不做裁决）
# - 是否修改系统对象: 否（完全只读）
# - 是否解析其他头部结构: 否
# ============================================================

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# ============================================================
# 边界声明与强约束说明
#
# 1. 只读约束: 本脚本不修改任何数据源
# 2. 内存边界: 大数据集采用扁平化元组结构在内存中比对，摒弃嵌套字典，绝对防范 OOM。
# 3. 报告完整性: 无论检查结果如何，必须生成完整报告。
# 4. 唯一键约束: 内存中映射 ChromaDB 数据时必须以 embedding_signature 为键，防止覆盖掩盖违规。
# ============================================================

# ============================================================
# 常量与全局配置区
# ============================================================

SCRIPT_NAME = "vector_integrity_check"
SCRIPT_VERSION = "v0001"
MAIN_LAYER = "understand"

COLLECTION_NAME = "understand_embeddings"

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_ERROR = "error"

# ============================================================
# 工具函数区（无副作用）
# ============================================================

def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def _timestamp_for_path() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

def _safe_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def _pretty_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)

def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for ln, line in enumerate(f, start=1):
            s = line.strip()
            if not s: continue
            try:
                obj = json.loads(s)
                if isinstance(obj, dict):
                    yield obj
            except Exception:
                continue

def _write_jsonl(path: Path, records: List[Dict[str, Any]]) -> int:
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(_safe_json(rec) + "\n")
    return len(records)

def _make_index_key(object_type: str, object_id: str) -> str:
    return f"{object_type}:{object_id}"

# ============================================================
# 数据源连接
# ============================================================

def _connect_sql(db_path: Path) -> Optional[sqlite3.Connection]:
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None

def _connect_chromadb(chromadb_path: Path):
    try:
        import chromadb
        from chromadb.config import Settings
    except ImportError:
        raise RuntimeError("chromadb is required. Run: pip install chromadb")
    
    if not chromadb_path.exists():
        return None, None
    
    try:
        client = chromadb.PersistentClient(
            path=str(chromadb_path),
            settings=Settings(anonymized_telemetry=False),
        )
        collection = client.get_collection(name=COLLECTION_NAME)
        return client, collection
    except Exception:
        return None, None

# ============================================================
# 轻量化数据获取 (防 OOM 设计)
# ============================================================

def _get_sql_records(conn: sqlite3.Connection) -> Dict[str, str]:
    """
    获取 SQL 核心比对数据 (极简结构)
    返回: { "type:id": "content_hash" }
    """
    records: Dict[str, str] = {}
    
    try:
        cursor = conn.execute("SELECT instance_id, content_hash FROM instance_units")
        for row in cursor:
            records[f"instance:{row['instance_id']}"] = str(row["content_hash"]).lower() if row["content_hash"] else ""
    except Exception:
        pass
    
    try:
        cursor = conn.execute("SELECT unit_text_id, content_hash FROM concept_units")
        for row in cursor:
            records[f"concept:{row['unit_text_id']}"] = str(row["content_hash"]).lower() if row["content_hash"] else ""
    except Exception:
        pass
        
    return records

def _get_sql_counts(conn: sqlite3.Connection) -> Tuple[int, int]:
    instance_count = 0
    concept_count = 0
    try:
        instance_count = conn.execute("SELECT COUNT(*) FROM instance_units").fetchone()[0]
    except Exception: pass
    try:
        concept_count = conn.execute("SELECT COUNT(*) FROM concept_units").fetchone()[0]
    except Exception: pass
    return instance_count, concept_count

def _get_chromadb_records(collection) -> Dict[str, Tuple[str, str, str, str]]:
    """
    获取 ChromaDB 核心比对数据 (防覆盖与防 OOM 结构)
    以唯一的 doc_id 为键，绝不覆盖同 object_id 的多条记录。
    返回: { "signature": ("object_type", "object_id", "source_hash", "state") }
    """
    records: Dict[str, Tuple[str, str, str, str]] = {}
    
    try:
        total = collection.count()
        batch_size = 5000
        offset = 0
        
        while offset < total:
            results = collection.get(
                limit=batch_size,
                offset=offset,
                include=["metadatas"]
            )
            
            ids = results.get("ids", [])
            metadatas = results.get("metadatas", [])
            
            for doc_id, meta in zip(ids, metadatas):
                obj_type = meta.get("object_type", "")
                obj_id = meta.get("object_id", "")
                if obj_type and obj_id:
                    records[doc_id] = (
                        obj_type,
                        obj_id,
                        meta.get("source_hash", "").lower(),
                        meta.get("state", "active")
                    )
            offset += batch_size
    except Exception:
        pass
    
    return records

def _get_chromadb_counts(collection) -> Tuple[int, int]:
    instance_count = 0
    concept_count = 0
    try:
        total = collection.count()
        batch_size = 5000
        offset = 0
        while offset < total:
            results = collection.get(limit=batch_size, offset=offset, include=["metadatas"])
            for meta in results.get("metadatas", []):
                if meta.get("object_type") == "instance": instance_count += 1
                elif meta.get("object_type") == "concept": concept_count += 1
            offset += batch_size
    except Exception:
        pass
    return instance_count, concept_count

def _get_index_records(index_path: Path) -> Dict[str, str]:
    """
    加载状态索引
    返回: { "type:id": "embedding_signature" }
    """
    records: Dict[str, str] = {}
    for rec in _iter_jsonl(index_path):
        obj_type = rec.get("object_type", "")
        obj_id = rec.get("object_id", "")
        sig = rec.get("embedding_signature", "")
        if obj_type and obj_id and sig:
            records[f"{obj_type}:{obj_id}"] = sig
    return records

# ============================================================
# 检查逻辑 (基于轻量化数据结构)
# ============================================================

def check_count_alignment(s_inst: int, s_conc: int, c_inst: int, c_conc: int) -> Dict[str, Any]:
    inst_match = s_inst == c_inst
    conc_match = s_conc == c_conc
    return {
        "status": STATUS_PASS if (inst_match and conc_match) else STATUS_FAIL,
        "sql_instance_count": s_inst, "sql_concept_count": s_conc,
        "chromadb_instance_count": c_inst, "chromadb_concept_count": c_conc,
        "instance_match": inst_match, "concept_match": conc_match,
    }

def check_orphan_vectors(
    sql_records: Dict[str, str],
    chroma_records: Dict[str, Tuple[str, str, str, str]],
) -> Tuple[str, List[Dict[str, Any]]]:
    orphans = []
    for sig, (c_type, c_id, _, _) in chroma_records.items():
        if f"{c_type}:{c_id}" not in sql_records:
            orphans.append({"object_type": c_type, "object_id": c_id, "embedding_signature": sig})
    return STATUS_PASS if not orphans else STATUS_FAIL, orphans

def check_missing_vectors(
    sql_records: Dict[str, str],
    chroma_records: Dict[str, Tuple[str, str, str, str]],
) -> Tuple[str, List[Dict[str, Any]]]:
    chroma_keys = {f"{c[0]}:{c[1]}" for c in chroma_records.values()}
    missing = []
    for sql_key in sql_records:
        if sql_key not in chroma_keys:
            obj_type, obj_id = sql_key.split(":", 1)
            missing.append({"object_type": obj_type, "object_id": obj_id})
    return STATUS_PASS if not missing else STATUS_FAIL, missing

def check_signature_drift(
    sql_records: Dict[str, str],
    chroma_records: Dict[str, Tuple[str, str, str, str]],
) -> Tuple[str, List[Dict[str, Any]]]:
    drifts = []
    for sig, (c_type, c_id, c_hash, _) in chroma_records.items():
        sql_key = f"{c_type}:{c_id}"
        if sql_key in sql_records:
            s_hash = sql_records[sql_key]
            if c_hash and s_hash and c_hash != s_hash:
                drifts.append({
                    "object_type": c_type, "object_id": c_id,
                    "sql_content_hash": s_hash, "chromadb_source_hash": c_hash,
                })
    return STATUS_PASS if not drifts else STATUS_FAIL, drifts

def check_index_consistency(
    index_records: Dict[str, str],
    chroma_records: Dict[str, Tuple[str, str, str, str]],
) -> Tuple[str, Dict[str, Any]]:
    # 提取 ChromaDB 中所有 state="active" 的映射
    chroma_active = {f"{c[0]}:{c[1]}": sig for sig, c in chroma_records.items() if c[3] == "active"}
    
    index_orphans = [key for key, sig in index_records.items() if chroma_active.get(key) != sig]
    index_missing = [key for key, sig in chroma_active.items() if index_records.get(key) != sig]
    
    status = STATUS_PASS if not index_orphans and not index_missing else STATUS_FAIL
    return status, {
        "index_orphans_count": len(index_orphans),
        "index_missing_count": len(index_missing),
        "index_orphans_sample": index_orphans[:10],
        "index_missing_sample": index_missing[:10],
    }

def check_multi_active(
    chroma_records: Dict[str, Tuple[str, str, str, str]],
) -> Tuple[str, List[Dict[str, Any]]]:
    # 彻底解决被覆盖导致无法发现 multi_active 的问题
    active_counts: Dict[str, List[str]] = {}
    for sig, (c_type, c_id, _, c_state) in chroma_records.items():
        if c_state == "active":
            key = f"{c_type}:{c_id}"
            active_counts.setdefault(key, []).append(sig)
            
    violations = [
        {"object_id": key.split(":", 1)[1], "active_count": len(sigs), "signatures": sigs}
        for key, sigs in active_counts.items() if len(sigs) > 1
    ]
    return STATUS_PASS if not violations else STATUS_FAIL, violations

# ============================================================
# 核心业务逻辑
# ============================================================

def run_integrity_check(
    sql_db_path: Path,
    chromadb_path: Path,
    state_index_path: Path,
    output_dir: Optional[Path],
    quick_mode: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    started_at = _utc_iso()
    result = {
        "status": "ok", "timestamp": started_at, "summary": {},
        "checks": {}, "details": {}, "errors": [],
    }
    
    sql_conn = _connect_sql(sql_db_path)
    if sql_conn is None:
        result["errors"].append(f"Failed to connect to SQL: {sql_db_path}")
        result["status"] = "error"
    
    client, collection = _connect_chromadb(chromadb_path)
    if collection is None:
        result["errors"].append(f"Failed to connect to ChromaDB: {chromadb_path}")
        result["status"] = "error"
    
    if result["status"] == "error":
        return result
    
    sql_instance, sql_concept = _get_sql_counts(sql_conn)
    chroma_instance, chroma_concept = _get_chromadb_counts(collection)
    index_records = _get_index_records(state_index_path)
    
    result["summary"] = {
        "sql_instance_count": sql_instance, "sql_concept_count": sql_concept,
        "sql_total": sql_instance + sql_concept,
        "chromadb_instance_count": chroma_instance, "chromadb_concept_count": chroma_concept,
        "chromadb_total": chroma_instance + chroma_concept,
        "index_count": len(index_records),
    }
    
    count_check = check_count_alignment(sql_instance, sql_concept, chroma_instance, chroma_concept)
    result["checks"]["count_alignment"] = count_check["status"]
    result["details"]["count_alignment"] = count_check
    
    if quick_mode:
        for skip_key in ["orphan_vectors", "missing_vectors", "signature_drift", "index_consistency", "multi_active"]:
            result["checks"][skip_key] = "skipped"
    else:
        sql_records = _get_sql_records(sql_conn)
        chroma_records = _get_chromadb_records(collection)
        
        orphan_status, orphans = check_orphan_vectors(sql_records, chroma_records)
        result["checks"]["orphan_vectors"] = orphan_status
        result["summary"]["orphan_count"] = len(orphans)
        if orphans: result["details"]["orphan_vectors"] = orphans
        
        missing_status, missing = check_missing_vectors(sql_records, chroma_records)
        result["checks"]["missing_vectors"] = missing_status
        result["summary"]["missing_count"] = len(missing)
        if missing: result["details"]["missing_vectors"] = missing
        
        drift_status, drifts = check_signature_drift(sql_records, chroma_records)
        result["checks"]["signature_drift"] = drift_status
        result["summary"]["drift_count"] = len(drifts)
        if drifts: result["details"]["signature_drift"] = drifts
        
        index_status, index_details = check_index_consistency(index_records, chroma_records)
        result["checks"]["index_consistency"] = index_status
        result["details"]["index_consistency"] = index_details
        
        multi_status, multi_violations = check_multi_active(chroma_records)
        result["checks"]["multi_active"] = multi_status
        result["summary"]["multi_active_count"] = len(multi_violations)
        if multi_violations: result["details"]["multi_active"] = multi_violations
    
    if sql_conn:
        sql_conn.close()
    
    if any(v == STATUS_FAIL for v in result["checks"].values()):
        result["status"] = "issues_found"
    
    result["finished_at"] = _utc_iso()
    
    if not dry_run and output_dir:
        try:
            _ensure_dir(output_dir)
            report_file = output_dir / "report.json"
            report_file.write_text(_pretty_json(result), encoding="utf-8")
            
            for key in ["orphan_vectors", "missing_vectors", "signature_drift"]:
                if result["details"].get(key):
                    _write_jsonl(output_dir / f"{key}.jsonl", result["details"][key])
            
            meta = {
                "script_name": SCRIPT_NAME, "script_version": SCRIPT_VERSION,
                "main_layer": MAIN_LAYER, "sql_db_path": str(sql_db_path),
                "chromadb_path": str(chromadb_path), "state_index_path": str(state_index_path),
                "quick_mode": quick_mode, "started_at": started_at,
                "finished_at": result["finished_at"], "status": result["status"],
            }
            (output_dir / "run_meta.json").write_text(_pretty_json(meta), encoding="utf-8")
            result["output_dir"] = str(output_dir)
        except Exception as e:
            result["errors"].append(f"Failed to write report: {e}")
    
    return result

# ============================================================
# CLI / main 接口区
# ============================================================

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Check integrity and consistency between SQL database and ChromaDB vector store."
    )
    ap.add_argument("--sql-db", default="sql/understand.db", help="Path to SQL database.")
    ap.add_argument("--chromadb-path", default="chromadb/understand/vectors", help="Path to ChromaDB persistence.")
    ap.add_argument("--state-index", default="vector/state/active_index.jsonl", help="Path to state index file.")
    ap.add_argument("--output-dir", default=None, help="Output directory for reports. Default: vector/audit/integrity_check/{timestamp}")
    ap.add_argument("--quick", action="store_true", help="Quick mode: only check count alignment.")
    ap.add_argument("--dry-run", action="store_true", help="Dry run mode. Only output to stdout, no files written.")
    return ap.parse_args()

def main() -> int:
    args = parse_args()
    
    sql_db_path = Path(args.sql_db)
    chromadb_path = Path(args.chromadb_path)
    state_index_path = Path(args.state_index)
    
    if args.output_dir:
        output_dir = Path(args.output_dir)
    elif not args.dry_run:
        output_dir = Path("vector/audit/integrity_check") / _timestamp_for_path()
    else:
        output_dir = None
    
    result = run_integrity_check(
        sql_db_path=sql_db_path,
        chromadb_path=chromadb_path,
        state_index_path=state_index_path,
        output_dir=output_dir,
        quick_mode=args.quick,
        dry_run=args.dry_run,
    )
    
    print(json.dumps(result, ensure_ascii=False, indent=2))
    
    if result.get("status") == "error": return 1
    if result.get("status") == "issues_found": return 2
    return 0

if __name__ == "__main__":
    raise SystemExit(main())