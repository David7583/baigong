#!/usr/bin/env python3
# ============================================================
# File: analyze_json_structure_v0002.py
# 中文名: JSON 结构探测脚本
# Version: v0002
# Layer: execution
# Main Layer: understand
# Updatable: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: analyze_json_structure
# family: analyze_json_structure
# role: json_structure_discovery
# version: v0002
# status: active
# entry_point: analyze_json_structure_v0002.py
# input:
#   - json working copy file
# output:
#   - structure report (json)
# depends_on:
#   - prepare_json_parse_task_v0001.py
# used_by:
#   - profile_string_values
# ============================================================

# ============================================================
# 制度与职责说明注释区
# ============================================================
# 是否进行合规判断: 否
# 是否进行制度判断: 否
# 是否修改系统对象: 否 (仅对解析任务工作台中的 JSON 副本进行只读访问，不修改、移动或删除任何输入文件)
# 是否解析其他头部结构: 否
# 
# 附加职责说明:
# 1. 本脚本用于探测 JSON 内部结构事实，在不引入语义假设前提下生成结构报告。
# 2. 本版本(v0002)已解除遍历深度限制，并支持对动态 UUID 键进行深度通配符(**)折叠。
# 3. 所有分析产出必须与被分析副本空间绑定，且对预算与能力边界如实标注 partial 或 error。
# 4. 本脚本属于执行型脚本 (Execution)，只输出判断结果与结构报告，不生成语义内容。
# ============================================================

from __future__ import annotations

# ============================================================
# Imports 区
# ============================================================
import argparse
import json
import sys
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Set

# 解除 Python 底层递归深度限制，允许极端深度的数据穿透
sys.setrecursionlimit(10000)


# ============================================================
# 常量与全局配置区
# ============================================================

# 解除硬性预算限制，设定为算力自由级别的探底阈值
DEFAULT_MAX_DEPTH = 9999
DEFAULT_MAX_NODES = 100_000_000
DEFAULT_MAX_LIST_SAMPLE = 999_999
DEFAULT_MAX_KEYS_SAMPLE = 999_999
DEFAULT_MAX_KEY_SAMPLE_STORE = 999_999

# 默认加载阈值放宽至 5GB
DEFAULT_MAX_LOAD_BYTES = 5 * 1024 * 1024 * 1024

STRUCTURE_DIR_NAME = "_analysis"
STRUCTURE_REPORT_NAME = "structure_report.json"

ROOT_PATH = "/"


# ============================================================
# 工具函数区（无副作用）
# ============================================================

# 动态键（如 UUID）嗅探正则
UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", 
    re.IGNORECASE
)

def is_dynamic_key(key: str) -> bool:
    """判断一个 Key 是否是动态生成的 ID (如 UUID)"""
    if UUID_PATTERN.match(key):
        return True
    return False

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)

def normalize_json_type(obj: Any) -> str:
    if obj is None:
        return "null"
    if isinstance(obj, bool):
        return "boolean"
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        return "number"
    if isinstance(obj, str):
        return "string"
    if isinstance(obj, dict):
        return "object"
    if isinstance(obj, list):
        return "array"
    return "unknown"

def safe_add_hist(hist: Dict[str, int], k: str, inc: int = 1) -> None:
    hist[k] = hist.get(k, 0) + inc

def join_path(parent: str, key: str) -> str:
    if parent == ROOT_PATH:
        return f"/{key}"
    return f"{parent}/{key}"

def array_pattern(parent: str) -> str:
    if parent == ROOT_PATH:
        return "/*"
    return f"{parent}/*"

def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a.intersection(b))
    union = len(a.union(b))
    return inter / union if union else 0.0

def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def unique_append_by_path(
    lst: List[Dict[str, Any]],
    item: Dict[str, Any],
    key_field: str = "path_pattern",
) -> None:
    val = item.get(key_field)
    if val is None:
        lst.append(item)
        return
    for x in lst:
        if x.get(key_field) == val:
            return
    lst.append(item)


# ============================================================
# 核心业务逻辑区
# ============================================================

def analyze_json_structure(
    *,
    json_path: Path,
    max_depth: int,
    max_nodes: int,
    max_list_sample: int,
    max_keys_sample: int,
    max_key_sample_store: int,
    max_load_bytes: int,
) -> Dict[str, Any]:

    report: Dict[str, Any] = {
        "file": json_path.name,
        "copy_path": str(json_path),
        "analyzed_at": utc_now_iso(),
        "script": {
            "name": "analyze_json_structure_v0002.py",
            "version": "v0002",
        },
        "budget": {
            "max_depth": max_depth,
            "max_nodes": max_nodes,
            "max_list_sample": max_list_sample,
            "max_keys_sample": max_keys_sample,
            "max_key_sample_store": max_key_sample_store,
            "max_load_bytes": max_load_bytes,
        },
        "file_meta": {
            "size_bytes": None,
        },
        "parse_status": "success",
        "root_type": None,
        "max_depth_seen": 0,
        "node_count_seen": 0,
        "type_histogram": {},
        "paths": [],
        "candidate_record_paths": [],
        "warnings": [],
        "truncated": False,
        "truncated_reason": None,
    }

    try:
        size_bytes = json_path.stat().st_size
        report["file_meta"]["size_bytes"] = size_bytes
    except Exception as e:
        report["parse_status"] = "read_error"
        report["warnings"].append(f"stat_failed: {e}")
        report["truncated"] = True
        report["truncated_reason"] = "stat_failed"
        return report

    if max_load_bytes > 0 and size_bytes > max_load_bytes:
        report["parse_status"] = "partial"
        report["warnings"].append(
            f"size_limit_exceeded: file_size={size_bytes} max_load_bytes={max_load_bytes}"
        )
        report["truncated"] = True
        report["truncated_reason"] = "size_limit"
        report["root_type"] = "unknown"
        return report

    try:
        data = load_json(json_path)
    except Exception as e:
        report["parse_status"] = "read_error"
        report["warnings"].append(f"read_error: {e}")
        report["truncated"] = True
        report["truncated_reason"] = "read_error"
        report["root_type"] = "unknown"
        return report

    report["root_type"] = normalize_json_type(data)

    path_stats: Dict[Tuple[str, str], Dict[str, Any]] = {}

    state = {
        "node_count": 0,
        "max_depth_seen": 0,
        "truncated": False,
        "truncated_reason": None,
        "type_hist": {},
        "warnings": [],
    }

    def touch_path_stat(path_pattern: str, node_type: str) -> Dict[str, Any]:
        key = (path_pattern, node_type)
        if key not in path_stats:
            path_stats[key] = {
                "path_pattern": path_pattern,
                "node_type": node_type,
                "seen_count": 0,
                "observed_child_types": {},
                "key_count_min": None,
                "key_count_max": None,
                "key_count_sum": 0,
                "key_count_seen": 0,
                "key_sample": [],
                "array_len_min": None,
                "array_len_max": None,
                "array_len_sum": 0,
                "array_len_seen": 0,
                "array_element_type_histogram": {},
                "array_sampled_elements_total": 0,
            }
        return path_stats[key]

    def update_obj_stats(stat: Dict[str, Any], obj: Dict[str, Any]) -> None:
        kcount = len(obj)
        stat["key_count_seen"] += 1
        stat["key_count_sum"] += kcount
        if stat["key_count_min"] is None or kcount < stat["key_count_min"]:
            stat["key_count_min"] = kcount
        if stat["key_count_max"] is None or kcount > stat["key_count_max"]:
            stat["key_count_max"] = kcount

        if max_keys_sample > 0:
            keys = list(obj.keys())[:max_keys_sample]
            existing = set(stat["key_sample"])
            for k in keys:
                if len(stat["key_sample"]) >= max_key_sample_store:
                    break
                if k not in existing:
                    stat["key_sample"].append(k)
                    existing.add(k)

    def update_array_stats(stat: Dict[str, Any], arr: List[Any]) -> None:
        alen = len(arr)
        stat["array_len_seen"] += 1
        stat["array_len_sum"] += alen
        if stat["array_len_min"] is None or alen < stat["array_len_min"]:
            stat["array_len_min"] = alen
        if stat["array_len_max"] is None or alen > stat["array_len_max"]:
            stat["array_len_max"] = alen

    def maybe_stop(depth: int) -> bool:
        if state["node_count"] >= max_nodes:
            state["truncated"] = True
            state["truncated_reason"] = "node_limit"
            return True
        if depth > max_depth:
            state["truncated"] = True
            state["truncated_reason"] = "depth_limit"
            return True
        return False

    def traverse(node: Any, path_pattern: str, depth: int) -> None:
        if maybe_stop(depth):
            return

        state["node_count"] += 1
        if depth > state["max_depth_seen"]:
            state["max_depth_seen"] = depth

        ntype = normalize_json_type(node)
        safe_add_hist(state["type_hist"], ntype, 1)

        stat = touch_path_stat(path_pattern, ntype)
        stat["seen_count"] += 1

        if isinstance(node, dict):
            update_obj_stats(stat, node)
            for k, v in list(node.items())[:max_keys_sample]:
                child_type = normalize_json_type(v)
                safe_add_hist(stat["observed_child_types"], child_type, 1)
                
                # 动态键嗅探与折叠逻辑
                key_str = str(k)
                if is_dynamic_key(key_str):
                    next_path = join_path(path_pattern, "**")
                else:
                    next_path = join_path(path_pattern, key_str)
                
                traverse(v, next_path, depth + 1)

        elif isinstance(node, list):
            update_array_stats(stat, node)
            sample_n = min(len(node), max_list_sample)
            if sample_n < len(node):
                state["truncated"] = True
                if state["truncated_reason"] is None:
                    state["truncated_reason"] = "list_sample_limit"

            elem_path = array_pattern(path_pattern)
            sampled = node[:sample_n]
            stat["array_sampled_elements_total"] += sample_n

            for elem in sampled:
                etype = normalize_json_type(elem)
                safe_add_hist(stat["array_element_type_histogram"], etype, 1)
                traverse(elem, elem_path, depth + 1)
        else:
            return

    traverse(data, ROOT_PATH, 0)

    report["max_depth_seen"] = state["max_depth_seen"]
    report["node_count_seen"] = state["node_count"]
    report["type_histogram"] = state["type_hist"]

    if state["truncated"]:
        report["parse_status"] = "partial"
        report["truncated"] = True
        report["truncated_reason"] = state["truncated_reason"]

    def find_record_candidates(node: Any, path_pattern: str, depth: int) -> None:
        if depth > 20: # [MODIFIED] 配合深层探底，适当放宽 candidate 验证的深度
            return
        if isinstance(node, dict):
            for k, v in node.items():
                key_str = str(k)
                next_path = join_path(path_pattern, "**") if is_dynamic_key(key_str) else join_path(path_pattern, key_str)
                find_record_candidates(v, next_path, depth + 1)
            return
        if isinstance(node, list):
            if len(node) <= 1:
                return

            sample_n = min(len(node), max_list_sample)
            sampled = node[:sample_n]
            obj_elems = [x for x in sampled if isinstance(x, dict)]
            obj_ratio = len(obj_elems) / sample_n if sample_n else 0.0

            confidence = "low"
            reason_parts: List[str] = []

            if obj_ratio >= 0.8 and len(obj_elems) >= 2:
                reason_parts.append("array_of_objects_high_ratio")
                base_keys = set(list(obj_elems[0].keys())[:max_keys_sample])
                sims: List[float] = []
                for elem in obj_elems[1: min(len(obj_elems), 6)]:
                    cur_keys = set(list(elem.keys())[:max_keys_sample])
                    sims.append(jaccard(base_keys, cur_keys))
                avg_sim = sum(sims) / len(sims) if sims else 0.0

                if avg_sim >= 0.6:
                    confidence = "high"
                    reason_parts.append("similar_object_key_signatures")
                elif avg_sim >= 0.4:
                    confidence = "medium"
                    reason_parts.append("partially_similar_object_key_signatures")
                else:
                    confidence = "low"
                    reason_parts.append("low_key_signature_similarity")

                unique_append_by_path(
                    report["candidate_record_paths"],
                    {
                        "path_pattern": array_pattern(path_pattern),
                        "array_length": len(node),
                        "sample_size": sample_n,
                        "object_ratio_in_sample": round(obj_ratio, 3),
                        "avg_key_signature_similarity": round(avg_sim, 3),
                        "confidence": confidence,
                        "reason": ",".join(reason_parts),
                    },
                )

            elem_path = array_pattern(path_pattern)
            for elem in sampled:
                find_record_candidates(elem, elem_path, depth + 1)

    find_record_candidates(data, ROOT_PATH, 0)

    paths_out: List[Dict[str, Any]] = []
    for (_pp, _nt), stat in path_stats.items():
        out = {
            "path_pattern": stat["path_pattern"],
            "node_type": stat["node_type"],
            "seen_count": stat["seen_count"],
            "observed_child_types": stat["observed_child_types"],
        }

        if stat["node_type"] == "object":
            key_count_avg = (
                stat["key_count_sum"] / stat["key_count_seen"]
                if stat["key_count_seen"] else None
            )
            out.update(
                {
                    "key_count_min": stat["key_count_min"],
                    "key_count_max": stat["key_count_max"],
                    "key_count_avg": round(key_count_avg, 3) if key_count_avg is not None else None,
                    "key_sample": stat["key_sample"],
                }
            )

        if stat["node_type"] == "array":
            array_len_avg = (
                stat["array_len_sum"] / stat["array_len_seen"]
                if stat["array_len_seen"] else None
            )
            out.update(
                {
                    "array_len_min": stat["array_len_min"],
                    "array_len_max": stat["array_len_max"],
                    "array_len_avg": round(array_len_avg, 3) if array_len_avg is not None else None,
                    "array_element_type_histogram": stat["array_element_type_histogram"],
                    "array_sampled_elements_total": stat["array_sampled_elements_total"],
                }
            )

        paths_out.append(out)

    paths_out.sort(key=lambda x: (x.get("path_pattern", ""), x.get("node_type", "")))
    report["paths"] = paths_out

    if report["root_type"] == "unknown":
        report["warnings"].append("root_type_unknown")

    if report["max_depth_seen"] >= max_depth:
        report["warnings"].append("hit_max_depth_budget")

    if report["node_count_seen"] >= max_nodes:
        report["warnings"].append("hit_max_nodes_budget")

    if report["type_histogram"].get("unknown", 0) > 0:
        report["warnings"].append("unknown_node_type_encountered")

    return report


# ============================================================
# CLI / main 接口区
# ============================================================

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Analyze structural patterns of a JSON file without semantic assumptions."
    )

    p.add_argument(
        "--json-file",
        required=True,
        help="Path to a JSON working copy file.",
    )

    p.add_argument(
        "--max-depth",
        type=int,
        default=DEFAULT_MAX_DEPTH,
        help="Maximum traversal depth.",
    )

    p.add_argument(
        "--max-nodes",
        type=int,
        default=DEFAULT_MAX_NODES,
        help="Maximum number of nodes to inspect.",
    )

    p.add_argument(
        "--max-list-sample",
        type=int,
        default=DEFAULT_MAX_LIST_SAMPLE,
        help="Maximum number of list elements to sample per array.",
    )

    p.add_argument(
        "--max-keys-sample",
        type=int,
        default=DEFAULT_MAX_KEYS_SAMPLE,
        help="Maximum number of keys to traverse per object.",
    )

    p.add_argument(
        "--max-key-sample-store",
        type=int,
        default=DEFAULT_MAX_KEY_SAMPLE_STORE,
        help="Maximum number of unique keys to store in key_sample per path.",
    )

    p.add_argument(
        "--max-load-bytes",
        type=int,
        default=DEFAULT_MAX_LOAD_BYTES,
        help="If file exceeds this size, report will be partial and JSON will not be loaded. Use 0 to disable.",
    )

    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Run analysis without writing any output.",
    )

    return p

def main() -> int:
    args = build_arg_parser().parse_args()

    json_path = Path(args.json_file).resolve()
    if not json_path.exists() or not json_path.is_file():
        raise SystemExit(f"Invalid json file: {json_path}")

    analysis_dir = json_path.parent / STRUCTURE_DIR_NAME
    report_path = analysis_dir / STRUCTURE_REPORT_NAME

    report = analyze_json_structure(
        json_path=json_path,
        max_depth=args.max_depth,
        max_nodes=args.max_nodes,
        max_list_sample=args.max_list_sample,
        max_keys_sample=args.max_keys_sample,
        max_key_sample_store=args.max_key_sample_store,
        max_load_bytes=args.max_load_bytes,
    )

    if args.dry_run:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    ensure_dir(analysis_dir)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"[OK] Structure report written to: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())