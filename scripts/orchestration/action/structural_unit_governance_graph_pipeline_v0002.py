#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# File: structural_unit_governance_graph_pipeline_v0002.py
# 中文名: Action结构单元治理与关系图编排兼容脚本
# Version: v0002
# Layer: orchestration
# Main Layer: Action
# Updatable: True
# 功能说明: 在保持v0001业务节点不变的前提下，接收v0002接纳清单的ready_for_action契约。
# Scope: 仅调整Data到Action的入口状态校验，继续执行既有21个结构治理节点。
#
# 职责说明:
# 1. 严格校验v0002接纳完成清单和ready_for_action Action lineage；
# 2. 复用已经验收的v0001结构治理执行逻辑；
# 3. 将本次运行身份和输出目录升级为v0002。
#
# 明确不做的事情:
# 1. 不结束Action lineage；
# 2. 不改变结构治理业务算法、字段或节点顺序；
# 3. 不接受Understanding lineage作为Action入口；
# 4. 不动态选择旧版结构编排。
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: structural_unit_governance_graph_pipeline
# family: structural_unit_governance_graph_pipeline
# role: action_structural_governance_orchestrator
# version: v0002
# status: active
# entry_point: scripts/orchestration/action/structural_unit_governance_graph_pipeline_v0002.py
# input:
#   - data_admission_lineage_pipeline v0002 completion manifest
#   - ready_for_action Action lineage record
# output:
#   - structural governance and graph completion manifest
# depends_on:
#   - scripts/orchestration/action/structural_unit_governance_graph_pipeline_v0001.py
# used_by:
#   - data_action_chain_pipeline
# governance:
#   level: middle
#   principle: compatible_contract_adapter
# ============================================================

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence


SCRIPT_NAME = "structural_unit_governance_graph_pipeline_v0002.py"
SCRIPT_VERSION = "v0002"
BASE_RELATIVE = Path("scripts") / "orchestration" / "action" / "structural_unit_governance_graph_pipeline_v0001.py"


def find_project_root() -> Path:
    for candidate in [Path.cwd(), *Path(__file__).resolve().parents]:
        if (candidate / "AGENTS.md").is_file():
            return candidate.resolve()
    raise RuntimeError("unable to locate project root")


def load_base_module(project_root: Path) -> Any:
    base_path = (project_root / BASE_RELATIVE).resolve()
    if not base_path.is_file():
        raise RuntimeError(f"pinned base orchestrator is missing: {base_path}")
    spec = importlib.util.spec_from_file_location("structural_pipeline_v0001_base", base_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load pinned base orchestrator: {base_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


PROJECT_ROOT = find_project_root()
BASE = load_base_module(PROJECT_ROOT)


def validate_ready_for_action_upstream(
    completion_path: Path,
    *,
    project_root: Path,
) -> Dict[str, Any]:
    completion = BASE.load_json_file(completion_path, label="upstream completion manifest")
    if completion.get("pipeline") != BASE.UPSTREAM_COMPLETION_FAMILY:
        raise BASE.PipelineError(f"unexpected prerequisite pipeline: {completion.get('pipeline')}")
    if completion.get("status") != "completed":
        raise BASE.PipelineError("prerequisite orchestration status must be completed")
    if completion.get("readiness") != "ready_for_action":
        raise BASE.PipelineError("prerequisite readiness must be ready_for_action")

    completion_run_id = completion.get("run_id")
    if not isinstance(completion_run_id, str) or not completion_run_id:
        raise BASE.PipelineError("upstream completion manifest is missing run_id")
    upstream_record = completion.get("upstream")
    if not isinstance(upstream_record, Mapping):
        raise BASE.PipelineError("upstream completion manifest is missing upstream record")

    handoff_path = BASE.resolve_manifest_path(completion, ("upstream", "handoff_manifest"), project_root=project_root, label="data preparation handoff manifest")
    handoff = BASE.load_json_file(handoff_path, label="data preparation handoff manifest")
    if handoff.get("pipeline") != BASE.UPSTREAM_HANDOFF_FAMILY:
        raise BASE.PipelineError(f"unexpected handoff pipeline: {handoff.get('pipeline')}")
    if handoff.get("status") != BASE.UPSTREAM_HANDOFF_STATUS:
        raise BASE.PipelineError(f"handoff status must be {BASE.UPSTREAM_HANDOFF_STATUS}")

    handoff_run_id = handoff.get("run_id")
    if not isinstance(handoff_run_id, str) or not handoff_run_id:
        raise BASE.PipelineError("data preparation handoff is missing run_id")
    if upstream_record.get("run_id") != handoff_run_id:
        raise BASE.PipelineError("completion manifest and handoff run_id do not match")

    working_copy = BASE.resolve_manifest_path(handoff, ("evidence", "working_copy"), project_root=project_root, label="upstream working copy")
    structure_report = BASE.resolve_manifest_path(handoff, ("evidence", "structure_report"), project_root=project_root, label="upstream structure report")
    asset_manifest = BASE.resolve_manifest_path(completion, ("admission", "asset_manifest"), project_root=project_root, label="admitted asset manifest")
    lineage_record = BASE.resolve_manifest_path(completion, ("lineage", "record_path"), project_root=project_root, label="Action lineage record")
    inventory_snapshot = BASE.resolve_manifest_path(completion, ("inventory", "snapshot"), project_root=project_root, label="post-admission inventory snapshot")

    structure = BASE.load_json_file(structure_report, label="upstream structure report")
    if structure.get("parse_status") != "success" or structure.get("truncated") is True:
        raise BASE.PipelineError("upstream structure report is not a complete successful parse")
    lineage = BASE.load_json_file(lineage_record, label="Action lineage record")
    if lineage.get("schema_version") != "action_lineage_record_v0002":
        raise BASE.PipelineError("Action lineage record schema must be v0002")
    if lineage.get("status") != "ready_for_action":
        raise BASE.PipelineError("Action lineage record must be ready_for_action")
    lineage_summary = completion.get("lineage")
    if not isinstance(lineage_summary, Mapping) or lineage_summary.get("type") != "action" or lineage_summary.get("status") != "ready_for_action":
        raise BASE.PipelineError("completion manifest does not declare ready Action lineage")
    if completion.get("inventory", {}).get("required_paths_observed") is not True:
        raise BASE.PipelineError("prerequisite inventory did not observe all required paths")

    slice_count = upstream_record.get("slice_count")
    admitted_count = completion.get("admission", {}).get("admitted_count")
    if not isinstance(slice_count, int) or slice_count < 1:
        raise BASE.PipelineError("completion manifest slice_count must be positive")
    if admitted_count != slice_count:
        raise BASE.PipelineError("admitted_count does not match upstream slice_count")
    return {
        "completion": completion,
        "completion_run_id": completion_run_id,
        "completion_manifest": completion_path,
        "handoff": handoff,
        "handoff_run_id": handoff_run_id,
        "handoff_manifest": handoff_path,
        "working_copy": working_copy,
        "structure_report": structure_report,
        "asset_manifest": asset_manifest,
        "lineage_record": lineage_record,
        "inventory_snapshot": inventory_snapshot,
        "slice_count": slice_count,
        "admitted_count": admitted_count,
    }


def configure_base() -> None:
    BASE.SCRIPT_NAME = SCRIPT_NAME
    BASE.SCRIPT_VERSION = SCRIPT_VERSION
    BASE.validate_completed_upstream = validate_ready_for_action_upstream


def build_parser() -> Any:
    configure_base()
    parser = BASE.build_parser()
    parser.description = "Reliably run Action structural governance from a ready_for_action admission contract."
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = BASE.execute_pipeline(args)
    except Exception as exc:
        print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
