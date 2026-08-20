#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# File: data_action_chain_pipeline_v0002.py
# 中文名: Data-Action-Data半循环总编排脚本
# Version: v0002
# Layer: orchestration
# Main Layer: Action
# Updatable: True
# 功能说明: 使用目标作用域已对齐的向量编排执行 Data -> Action -> Data 半循环。
# Scope: v0001 总编排行为保持不变，仅升级向量子编排接口版本。
#
# 明确不做的事情:
# 1. 不自动进入 Understanding，不形成无限循环。
# 2. 不修改各业务节点及 Action lineage 语义。
# 3. 不绕过数据库、向量写入和模型调用的显式授权。
# ============================================================

# ============================================================
# ALIAS_META
# alias: data_action_chain_pipeline
# family: data_action_chain_pipeline
# role: data_action_data_cycle_orchestrator
# version: v0002
# status: active
# entry_point: scripts/orchestration/action/data_action_chain_pipeline_v0002.py
# input: one data_raw target and explicit database/vector paths
# output: completed half-cycle manifests and Action return manifest
# depends_on: scripts/orchestration/action/data_action_chain_pipeline_v0001.py, scripts/orchestration/action/vector_embedding_pipeline_v0002.py
# used_by: future total state machine
# ============================================================

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Mapping


SCRIPT_NAME = "data_action_chain_pipeline_v0002.py"
SCRIPT_FAMILY = "data_action_chain_pipeline"
SCRIPT_VERSION = "v0002"


def _find_project_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "AGENTS.md").is_file() and (candidate / "scripts").is_dir():
            return candidate
    raise RuntimeError(f"Cannot locate project root from: {start}")


def _load_v0001(project_root: Path):
    source = project_root / "scripts" / "orchestration" / "action" / "data_action_chain_pipeline_v0001.py"
    spec = importlib.util.spec_from_file_location("data_action_chain_pipeline_v0001_base", source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load compatibility base: {source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    project_root = _find_project_root(Path(__file__).resolve().parent)
    base = _load_v0001(project_root)
    original_completion_path = base.completion_path_from_result

    def completion_path_from_result(
        result: Mapping[str, Any], *, output_root: Path, family: str, version: str, run_id: str
    ) -> Path:
        effective_version = "v0002" if family == "vector_embedding_pipeline" else version
        return original_completion_path(
            result,
            output_root=output_root,
            family=family,
            version=effective_version,
            run_id=run_id,
        )

    base.SCRIPT_NAME = SCRIPT_NAME
    base.SCRIPT_VERSION = SCRIPT_VERSION
    staged_vector = Path(__file__).resolve().with_name("vector_embedding_pipeline_v0002.py")
    base.PINNED_SCRIPTS["vector"] = str(
        staged_vector
        if staged_vector.is_file()
        else Path("scripts/orchestration/action/vector_embedding_pipeline_v0002.py")
    )
    original_run_step = base.PipelineRun.run_step
    completed_vector_targets: set[str] = set()

    def run_step(self, name: str, key: str, args: list[str]):
        effective_args = list(args)
        if key == "vector" and "--target" in effective_args:
            target_index = effective_args.index("--target")
            target = effective_args[target_index + 1]
            if target == "instance" and "concept" in completed_vector_targets:
                effective_args.extend(["--integrity-target", "all"])
            result = original_run_step(self, name, key, effective_args)
            completed_vector_targets.add(target)
            return result
        return original_run_step(self, name, key, effective_args)

    base.PipelineRun.run_step = run_step
    base.completion_path_from_result = completion_path_from_result
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
