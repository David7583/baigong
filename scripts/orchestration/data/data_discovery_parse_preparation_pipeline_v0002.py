#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# 文件名: data_discovery_parse_preparation_pipeline_v0002.py
# 中文名: 规范入口数据发现与解析准备兼容编排脚本
# 版本号: v0002
#
# 主层级: data
# 层级: orchestration / data / discovery
# 脚本定位: 保留 v0001 可靠发现链并将格式入口升级为完整规范契约
#
# 职责说明:
# - 只接受已经过入口网关契约校验的 canonical_conversation_ingress_v0001
# - 复用 v0001 发现、快照、工作副本、结构分析和 handoff 证据流程
#
# 本脚本做什么:
# - 将原先硬编码的 conversation_id+messages 判定替换为规范根、conversations 和完整对话字段判定
# - 固定使用 coarse_slice_conversations_v0002 保真切片规范对话
#
# 本脚本不做什么:
# - 不识别或适配外部格式，不接受未套信封的原始厂商数据
# - 不改变 v0001 的副作用、handoff 语义和停止边界
#
# 制度边界声明:
# - 该脚本不将 messages、mapping 或任一 Provider 字段作为入口条件
# - 旧 v0001 原样保留；本版本只被 v0008 新入口链调用
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: data_discovery_parse_preparation_pipeline_v0002
# family: data_discovery_parse_preparation_pipeline
# role: canonical_data_discovery_preparation_orchestrator
# version: v0002
# status: experimental
# entry_point: scripts/orchestration/data/data_discovery_parse_preparation_pipeline_v0002.py
# input:
#   - canonical_conversation_ingress_v0001 target under an isolated data root
# output:
#   - ready_for_admission handoff with canonical conversation slices
# depends_on:
#   - data_discovery_parse_preparation_pipeline_v0001
#   - coarse_slice_conversations_v0002
# used_by:
#   - data_action_chain_pipeline_v0008
# ============================================================

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "data_discovery_parse_preparation_pipeline"
SCRIPT_NAME = "data_discovery_parse_preparation_pipeline_v0002.py"
SCRIPT_VERSION = "v0002"
BASE_RELATIVE = Path("scripts") / "orchestration" / "data" / "data_discovery_parse_preparation_pipeline_v0001.py"
CANONICAL_SCHEMA_VERSION = "canonical_conversation_ingress_v0001"
CANONICAL_CONVERSATION_FIELDS = {
    "conversation_id", "title", "created_time", "updated_time", "current_node_id",
    "nodes", "metadata", "source_ref", "extensions",
}
CANONICAL_TEXT_PATH = "/conversations/*/nodes/*/message/content/parts/*/text"


# ============================================================
# 异常类型
# ============================================================

class CanonicalDiscoveryV2Error(RuntimeError):
    """Raised when the v0001 discovery base cannot be configured for canonical ingress."""


# ============================================================
# 数据结构
# ============================================================

# v0002 preserves the v0001 JSON handoff and run-manifest structures.


# ============================================================
# 工具函数区
# ============================================================

def _find_project_root() -> Path:
    for candidate in (Path.cwd().resolve(), *Path(__file__).resolve().parents):
        if (candidate / "AGENTS.md").is_file() and (candidate / "scripts").is_dir():
            return candidate
    raise CanonicalDiscoveryV2Error("cannot locate project root")


def _load_base(project_root: Path) -> Any:
    path = (project_root / BASE_RELATIVE).resolve()
    if not path.is_file():
        raise CanonicalDiscoveryV2Error(f"v0001 discovery base is missing: {path}")
    spec = importlib.util.spec_from_file_location("data_discovery_parse_preparation_v0001_base", path)
    if spec is None or spec.loader is None:
        raise CanonicalDiscoveryV2Error(f"cannot load v0001 discovery base: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ============================================================
# 默认映射
# ============================================================

PROJECT_ROOT = _find_project_root()
BASE = _load_base(PROJECT_ROOT)


# ============================================================
# 核心兼容组件
# ============================================================

def validate_canonical_structure(report: Mapping[str, Any], *, max_conversations: int) -> int:
    if report.get("parse_status") != "success":
        raise BASE.PipelineError(f"structure analysis did not succeed: {report.get('parse_status')}")
    if report.get("truncated"):
        raise BASE.PipelineError("structure analysis was truncated")
    if report.get("root_type") != "object":
        raise BASE.PipelineError("canonical ingress root must be an object")
    paths = report.get("paths")
    if not isinstance(paths, list):
        raise BASE.PipelineError("structure report paths must be a list")
    root_entry: Optional[Mapping[str, Any]] = None
    conversations_entry: Optional[Mapping[str, Any]] = None
    conversation_entry: Optional[Mapping[str, Any]] = None
    for item in paths:
        if not isinstance(item, Mapping):
            continue
        pattern = item.get("path_pattern")
        node_type = item.get("node_type")
        if pattern == "/" and node_type == "object":
            root_entry = item
        elif pattern == "/conversations" and node_type == "array":
            conversations_entry = item
        elif pattern == "/conversations/*" and node_type == "object":
            conversation_entry = item
    if root_entry is None or conversations_entry is None or conversation_entry is None:
        raise BASE.PipelineError("structure is not canonical ingress with a conversations array")
    root_keys = set(root_entry.get("key_sample") or [])
    if {"schema_version", "dataset_id", "source_envelope_id", "conversations"} - root_keys:
        raise BASE.PipelineError("canonical root is missing contract identity fields")
    conversation_keys = set(conversation_entry.get("key_sample") or [])
    missing = sorted(CANONICAL_CONVERSATION_FIELDS - conversation_keys)
    if missing:
        raise BASE.PipelineError(f"canonical conversation records are missing fields: {missing}")
    count = conversations_entry.get("array_len_max")
    if not isinstance(count, int) or count < 1:
        raise BASE.PipelineError("canonical conversations array is missing or empty")
    if count > max_conversations:
        raise BASE.PipelineError(f"conversation count {count} exceeds safety limit {max_conversations}")
    return count


def configure_base() -> None:
    BASE.SCRIPT_NAME = SCRIPT_NAME
    BASE.SCRIPT_VERSION = SCRIPT_VERSION
    BASE.PINNED_SCRIPTS = dict(BASE.PINNED_SCRIPTS)
    BASE.PINNED_SCRIPTS["slice"] = "coarse_slice_conversations_v0002.py"
    BASE.validate_conversation_structure = validate_canonical_structure


def _write_content_structure_handoff(result: Mapping[str, Any]) -> Mapping[str, Any]:
    handoff_path = Path(str(result.get("handoff_manifest", ""))).resolve()
    handoff = BASE.load_json_file(handoff_path, label="canonical discovery handoff")
    evidence = handoff.get("evidence")
    if not isinstance(evidence, Mapping):
        raise BASE.PipelineError("canonical discovery handoff is missing evidence")
    source_report_path = Path(str(evidence.get("structure_report", ""))).resolve()
    report = BASE.load_json_file(source_report_path, label="complete canonical structure report")
    paths = report.get("paths")
    if not isinstance(paths, list):
        raise BASE.PipelineError("complete canonical structure report has no paths")
    selected = [
        dict(item) for item in paths
        if isinstance(item, Mapping)
        and item.get("path_pattern") == CANONICAL_TEXT_PATH
        and item.get("node_type") == "string"
    ]
    if len(selected) != 1:
        raise BASE.PipelineError("canonical content text path is missing or ambiguous")
    filtered_report = {
        **report,
        "schema_version": "canonical_content_structure_report_v0001",
        "paths": selected,
        "path_scope": {
            "contract": CANONICAL_SCHEMA_VERSION,
            "included": [CANONICAL_TEXT_PATH],
            "excluded_policy": "metadata_extensions_and_source_refs_are_not_language_content",
            "complete_structure_report": str(source_report_path),
        },
    }
    filtered_path = source_report_path.with_name("canonical_content_structure_report.json")
    BASE.write_json_atomic(filtered_path, filtered_report)
    handoff["evidence"] = {**dict(evidence), "complete_structure_report": str(source_report_path), "structure_report": str(filtered_path)}
    handoff["content_path_contract"] = {
        "schema_version": "canonical_content_path_contract_v0001",
        "included_paths": [CANONICAL_TEXT_PATH],
        "status": "completed",
    }
    BASE.write_json_atomic(handoff_path, handoff)
    return {**dict(result), "content_structure_report": str(filtered_path), "canonical_text_paths": [CANONICAL_TEXT_PATH]}


def execute(args: Any) -> Mapping[str, Any]:
    configure_base()
    result = BASE.execute_pipeline(args)
    if args.dry_run:
        return result
    return _write_content_structure_handoff(result)


# ============================================================
# Schema / 契约辅助函数
# ============================================================

def discovery_contract() -> Mapping[str, Any]:
    return {
        "input_schema": CANONICAL_SCHEMA_VERSION,
        "provider_fields_required": [],
        "slicer": "coarse_slice_conversations_v0002.py",
        "language_content_paths": [CANONICAL_TEXT_PATH],
    }


# ============================================================
# CLI / main 接口区
# ============================================================

def build_parser() -> Any:
    configure_base()
    parser = BASE.build_parser()
    parser.description = "Prepare a complete canonical conversation ingress payload for admission."
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = execute(args)
    except Exception as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False, separators=(",", ":")), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
