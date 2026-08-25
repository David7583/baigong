#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# 文件名: data_admission_lineage_pipeline_v0003.py
# 中文名: 规范入口数据准入与血缘编排脚本
# 版本号: v0003
#
# 主层级: orchestration
# 层级: scripts / orchestration / data
# 脚本定位: 在 v0002 准入链上强制接入完整入口信封
#
# 职责说明:
# - 复用既有准入、Action 血缘和清点流程
# - 要求导入器及血缘证据共同携带 canonical ingress envelope
#
# 本脚本做什么:
# - 校验入口信封状态与契约有效性
# - 将信封传给 v0002 importer，并写入准入完成凭证
#
# 本脚本不做什么:
# - 不识别原始格式，不修改切片或已准入内容
# - 不降低 v0002 的确认、测试目录和覆盖保护
#
# 制度边界声明:
# - Provider 和格式身份只能来自入口信封，不允许编排器写死
# - 入口信封无效时必须在准入产生副作用前停止
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: data_admission_lineage_pipeline_v0003
# family: data_admission_lineage_pipeline
# role: canonical_ingress_admission_orchestrator
# version: v0003
# status: experimental
# entry_point: scripts/orchestration/data/data_admission_lineage_pipeline_v0003.py
# input:
#   - discovery handoff and canonical ingress envelope
# output:
#   - admitted assets, Action lineage, inventory and completion manifest
# depends_on:
#   - data_admission_lineage_pipeline_v0002.py
# used_by:
#   - data_action_chain_pipeline_v0008.py
# ============================================================

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Mapping, Optional, Sequence


SCRIPT_NAME = "data_admission_lineage_pipeline_v0003.py"
SCRIPT_VERSION = "v0003"
ENVELOPE_SCHEMA = "canonical_ingress_envelope_v0001"
BASE_RELATIVE = Path("scripts/orchestration/data/data_admission_lineage_pipeline_v0002.py")


def _find_project_root() -> Path:
    for candidate in [Path.cwd(), *Path(__file__).resolve().parents]:
        if (candidate / "AGENTS.md").is_file():
            return candidate.resolve()
    raise RuntimeError("unable to locate project root")


def _load_base(project_root: Path) -> ModuleType:
    path = (project_root / BASE_RELATIVE).resolve()
    if not path.is_file():
        raise RuntimeError(f"base admission pipeline is missing: {path}")
    spec = importlib.util.spec_from_file_location("data_admission_lineage_pipeline_base_v0002", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load base admission pipeline: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_envelope(base: ModuleType, project_root: Path, raw_path: str) -> tuple[Path, Dict[str, Any]]:
    path = base.resolve_from_project(project_root, raw_path, label="ingress envelope")
    envelope = base.load_json_file(path, label="ingress envelope")
    if envelope.get("schema_version") != ENVELOPE_SCHEMA:
        raise base.PipelineError(f"ingress envelope schema_version must be {ENVELOPE_SCHEMA}")
    if envelope.get("status") != "completed":
        raise base.PipelineError("ingress envelope status must be completed")
    canonical = envelope.get("canonical_output")
    if not isinstance(canonical, Mapping) or canonical.get("contract_valid") is not True:
        raise base.PipelineError("ingress envelope canonical contract is not valid")
    source = envelope.get("source")
    if not isinstance(source, Mapping) or source.get("immutable_verified") is not True:
        raise base.PipelineError("ingress envelope immutable source verification failed")
    return path, envelope


def _configure_base(base: ModuleType, ingress_path: Path) -> None:
    original_run = base.PipelineRun
    base.SCRIPT_NAME = SCRIPT_NAME
    base.SCRIPT_VERSION = SCRIPT_VERSION
    base.PINNED_SCRIPTS = {
        **dict(base.PINNED_SCRIPTS),
        "import": "scripts/import_conversation_slices_to_processed_v0002.py",
    }

    class CanonicalAdmissionRun(original_run):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.manifest["schema_version"] = "data_admission_lineage_run_v0003"
            self.manifest["ingress_envelope"] = str(ingress_path)

        def run_step(self, name: str, script_key: str, arguments: Sequence[str]) -> str:
            effective = list(arguments)
            if script_key == "import":
                effective.extend(["--ingress-envelope", str(ingress_path)])
            elif script_key == "lineage":
                effective.extend(["--evidence", f"ingress_envelope:{ingress_path}"])
            return super().run_step(name, script_key, effective)

    base.PipelineRun = CanonicalAdmissionRun


def execute_pipeline(args: Any) -> Dict[str, Any]:
    project_root = _find_project_root()
    base = _load_base(project_root)
    effective_root = base.find_project_root(args.project_root)
    ingress_path, envelope = _load_envelope(base, effective_root, args.ingress_envelope)
    _configure_base(base, ingress_path)
    result = base.execute_pipeline(args)
    if args.dry_run:
        result["ingress_envelope"] = str(ingress_path)
        result["source_envelope_id"] = envelope.get("envelope_id")
        return result
    completion_path = Path(str(result["completion_manifest"]))
    completion = base.load_json_file(completion_path, label="completion manifest")
    completion["schema_version"] = "data_admission_lineage_completion_v0003"
    completion["ingress"] = {
        "envelope_path": str(ingress_path),
        "envelope_id": envelope.get("envelope_id"),
        "format_family": envelope.get("detection", {}).get("format_family"),
        "fingerprint_version": envelope.get("detection", {}).get("fingerprint_version"),
        "adapter_family": envelope.get("adapter", {}).get("family"),
        "adapter_version": envelope.get("adapter", {}).get("version"),
    }
    base.write_json_atomic(completion_path, completion)
    result["ingress_envelope"] = str(ingress_path)
    result["source_envelope_id"] = envelope.get("envelope_id")
    return result


def build_parser() -> Any:
    base = _load_base(_find_project_root())
    parser = base.build_parser()
    parser.description = "Admit canonical conversation slices with an immutable complete-contract ingress envelope."
    parser.add_argument("--ingress-envelope", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = execute_pipeline(args)
    except Exception as exc:
        print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
