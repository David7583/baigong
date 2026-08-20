#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# File: action_anchor_persistence_pipeline_v0002.py
# 中文名: Action锚点持久化编排兼容脚本
# Version: v0002
# Layer: orchestration
# Main Layer: Action
# Updatable: True
# 功能说明: 接收结构编排v0002完成清单，并复用已验收的v0001锚点持久化节点。
# Scope: 仅升级上游结构编排版本契约和本次运行版本身份。
#
# 职责说明:
# 1. 固定接收structural_unit_governance_graph_pipeline v0002；
# 2. 保持锚点声明、校验、SQLite写入和完整性检查逻辑不变；
# 3. 输出v0002锚点编排完成清单。
#
# 明确不做的事情:
# 1. 不改变SQLite表结构或锚点字段语义；
# 2. 不绕过测试模式、写入确认或数据锚点门禁；
# 3. 不修改或删除v0001实现。
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: action_anchor_persistence_pipeline
# family: action_anchor_persistence_pipeline
# role: action_anchor_persistence_orchestrator
# version: v0002
# status: active
# entry_point: scripts/orchestration/action/action_anchor_persistence_pipeline_v0002.py
# input:
#   - structural_unit_governance_graph_pipeline v0002 completion manifest
# output:
#   - Action anchor persistence completion manifest
# depends_on:
#   - scripts/orchestration/action/action_anchor_persistence_pipeline_v0001.py
# used_by:
#   - data_action_chain_pipeline
# governance:
#   level: high
#   principle: compatible_contract_adapter
# ============================================================

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence


SCRIPT_NAME = "action_anchor_persistence_pipeline_v0002.py"
SCRIPT_VERSION = "v0002"
BASE_RELATIVE = Path("scripts") / "orchestration" / "action" / "action_anchor_persistence_pipeline_v0001.py"


def find_project_root() -> Path:
    for candidate in [Path.cwd(), *Path(__file__).resolve().parents]:
        if (candidate / "AGENTS.md").is_file():
            return candidate.resolve()
    raise RuntimeError("unable to locate project root")


def load_base(project_root: Path) -> Any:
    path = (project_root / BASE_RELATIVE).resolve()
    if not path.is_file():
        raise RuntimeError(f"pinned base orchestrator is missing: {path}")
    spec = importlib.util.spec_from_file_location("action_anchor_pipeline_v0001_base", path)
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
    BASE.UPSTREAM_VERSION = "v0002"


def build_parser() -> Any:
    configure_base()
    parser = BASE.build_parser()
    parser.description = "Reliably persist Action anchors from structural pipeline v0002."
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = BASE.execute(args)
    except Exception as exc:
        print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
