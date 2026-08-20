#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# File: vector_embedding_pipeline_v0002.py
# 中文名: Action 向量生成与入库可靠编排脚本
# Version: v0002
# Layer: orchestration
# Main Layer: Action
# Updatable: True
# 功能说明: 在 v0001 可靠串联基础上，将当前向量目标传给作用域感知的完整性检查。
# Scope: 从现有 Action SQL 文本真源开始，止于单目标写后核验及运行证据。
#
# 明确不做的事情:
# 1. 不改变生成、契约校验、生命周期判定或写入逻辑。
# 2. 不降低独立全局完整性审计标准。
# 3. 不调用付费 API，不删除或重建向量库。
# ============================================================

# ============================================================
# ALIAS_META
# alias: vector_embedding_pipeline
# family: vector_embedding_pipeline
# role: action_vector_orchestrator
# version: v0002
# status: active
# entry_point: scripts/orchestration/action/vector_embedding_pipeline_v0002.py
# input: Action SQL text source, embedding config and target selection
# output: Target-scoped vector pipeline evidence and completion/failure manifest
# depends_on: scripts/orchestration/action/vector_embedding_pipeline_v0001.py, scripts/action/vector/vector_integrity_check_v0002.py
# used_by: scripts/orchestration/action/data_action_chain_pipeline_v0002.py
# ============================================================

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


SCRIPT_NAME = "vector_embedding_pipeline_v0002.py"
SCRIPT_FAMILY = "vector_embedding_pipeline"
SCRIPT_VERSION = "v0002"
TARGET_ENV = "ACTION_VECTOR_INTEGRITY_TARGET"


def _find_project_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "AGENTS.md").is_file() and (candidate / "scripts").is_dir():
            return candidate
    raise RuntimeError(f"Cannot locate project root from: {start}")


def _load_v0001(project_root: Path):
    source = project_root / "scripts" / "orchestration" / "action" / "vector_embedding_pipeline_v0001.py"
    spec = importlib.util.spec_from_file_location("vector_embedding_pipeline_v0001_base", source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load compatibility base: {source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _target_from_argv(argv: list[str]) -> str:
    try:
        index = argv.index("--target")
        target = argv[index + 1]
    except (ValueError, IndexError):
        return "all"
    return target if target in {"concept", "instance"} else "all"


def _pop_integrity_target(argv: list[str], default: str) -> str:
    try:
        index = argv.index("--integrity-target")
    except ValueError:
        return default
    try:
        target = argv[index + 1]
    except IndexError as exc:
        raise RuntimeError("--integrity-target requires a value") from exc
    if target not in {"all", "concept", "instance"}:
        raise RuntimeError(f"Unsupported integrity target: {target}")
    del argv[index:index + 2]
    return target


def main() -> int:
    project_root = _find_project_root(Path(__file__).resolve().parent)
    base = _load_v0001(project_root)
    target = _target_from_argv(sys.argv[1:])
    integrity_target = _pop_integrity_target(sys.argv, target)
    if target == "instance" and "--asset-id" not in sys.argv and "--all-assets" not in sys.argv:
        sys.argv.append("--all-assets")
    os.environ[TARGET_ENV] = integrity_target
    base.SCRIPT_NAME = SCRIPT_NAME
    base.SCRIPT_VERSION = SCRIPT_VERSION
    staged_integrity = (
        Path(__file__).resolve().parents[2]
        / "action" / "vector" / "vector_integrity_check_v0002.py"
    )
    base.PINNED_SCRIPTS["integrity"] = (
        staged_integrity
        if staged_integrity.is_file()
        else base.VECTOR_DIR / "vector_integrity_check_v0002.py"
    )
    return base.cli()


if __name__ == "__main__":
    raise SystemExit(main())
