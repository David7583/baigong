#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# File: action_derivation_materialization_pipeline_v0003.py
# 中文名: Action语义锚点派生物化编排兼容脚本
# Version: v0003
# Layer: orchestration
# Main Layer: Action
# Updatable: True
# 功能说明: 接收v0003正文锚点与v0002结构完成清单，复用既有派生节点。
#
# ALIAS_META
# alias: action_derivation_materialization_pipeline
# family: action_derivation_materialization_pipeline
# role: action_derivation_materialization_orchestrator
# version: v0003
# status: active
# entry_point: scripts/orchestration/action/action_derivation_materialization_pipeline_v0003.py
# input: action_anchor_persistence_pipeline v0003 and structural pipeline v0002 manifests
# output: Action derivation materialization completion manifest
# depends_on: scripts/orchestration/action/action_derivation_materialization_pipeline_v0001.py
# used_by: data_action_chain_pipeline
# ============================================================

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Optional, Sequence


SCRIPT_NAME = "action_derivation_materialization_pipeline_v0003.py"
SCRIPT_VERSION = "v0003"
BASE_RELATIVE = Path("scripts") / "orchestration" / "action" / "action_derivation_materialization_pipeline_v0001.py"


def find_project_root() -> Path:
    for candidate in [Path.cwd(), *Path(__file__).resolve().parents]:
        if (candidate / "AGENTS.md").is_file() and (candidate / "scripts").is_dir():
            return candidate.resolve()
    raise RuntimeError("unable to locate project root")


def load_base(project_root: Path) -> Any:
    path = (project_root / BASE_RELATIVE).resolve()
    if not path.is_file():
        raise RuntimeError(f"pinned base orchestrator is missing: {path}")
    spec = importlib.util.spec_from_file_location("action_derivation_pipeline_v0001_semantic_base", path)
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
    BASE.ANCHOR_VERSION = "v0003"
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
