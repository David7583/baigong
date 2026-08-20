#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# File: action_derivation_materialization_pipeline_v0002.py
# 中文名: Action派生物化编排兼容脚本
# Version: v0002
# Layer: orchestration
# Main Layer: Action
# Updatable: True
# 功能说明: 接收结构与锚点编排v0002完成清单，复用v0001派生物化业务节点。
# Scope: 仅升级两个上游编排版本契约和本次运行版本身份。
#
# 职责说明:
# 1. 固定接收结构编排v0002和锚点编排v0002；
# 2. 保持DuckDB与Neo4j节点、门禁和写入确认逻辑不变；
# 3. 输出v0002派生物化完成清单。
#
# 明确不做的事情:
# 1. 不改变四库身份字段或派生语义；
# 2. 不绕过外部服务可用性和显式写入确认；
# 3. 不修改或删除v0001实现。
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: action_derivation_materialization_pipeline
# family: action_derivation_materialization_pipeline
# role: action_derivation_materialization_orchestrator
# version: v0002
# status: active
# entry_point: scripts/orchestration/action/action_derivation_materialization_pipeline_v0002.py
# input:
#   - action_anchor_persistence_pipeline v0002 completion manifest
#   - structural_unit_governance_graph_pipeline v0002 completion manifest
# output:
#   - Action derivation materialization completion manifest
# depends_on:
#   - scripts/orchestration/action/action_derivation_materialization_pipeline_v0001.py
# used_by:
#   - data_action_chain_pipeline
# governance:
#   level: high
#   principle: compatible_contract_adapter
# ============================================================

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Optional, Sequence


SCRIPT_NAME = "action_derivation_materialization_pipeline_v0002.py"
SCRIPT_VERSION = "v0002"
BASE_RELATIVE = Path("scripts") / "orchestration" / "action" / "action_derivation_materialization_pipeline_v0001.py"


def find_project_root() -> Path:
    for candidate in [Path.cwd(), *Path(__file__).resolve().parents]:
        if (candidate / "AGENTS.md").is_file():
            return candidate.resolve()
    raise RuntimeError("unable to locate project root")


def load_base(project_root: Path) -> Any:
    path = (project_root / BASE_RELATIVE).resolve()
    if not path.is_file():
        raise RuntimeError(f"pinned base orchestrator is missing: {path}")
    spec = importlib.util.spec_from_file_location("action_derivation_pipeline_v0001_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load pinned base orchestrator: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BASE = load_base(find_project_root())


def configure_base() -> None:
    BASE.SCRIPT_NAME = SCRIPT_NAME
    BASE.SCRIPT_VERSION = SCRIPT_VERSION
    BASE.ANCHOR_VERSION = "v0002"
    BASE.STRUCTURAL_VERSION = "v0002"


def main(argv: Optional[Sequence[str]] = None) -> int:
    configure_base()
    if argv is None:
        return int(BASE.main())
    original_argv = sys.argv
    try:
        sys.argv = [SCRIPT_NAME, *argv]
        return int(BASE.main())
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    raise SystemExit(main())
